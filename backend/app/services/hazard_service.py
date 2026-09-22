"""隐患登记与整改跟踪业务逻辑（含状态流转规则）。"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ConflictError, InvalidOperationError, NotFoundError
from app.db.base import now_local
from app.models import Hazard, HazardRectification, Inspection
from app.models.enums import HazardStatus, RectificationAction
from app.schemas.hazard import (
    AssigneeOption,
    BatchFailureItem,
    HazardBatchAssignRequest,
    HazardBatchResult,
    HazardBatchUrgeRequest,
    HazardCreate,
    HazardRectificationCreate,
    HazardTransitionOption,
    HazardTransitionRequest,
    HazardUpdate,
)
from app.services import reservoir_service
from app.services.helpers import enum_to_value, next_code


@dataclass(frozen=True)
class TransitionRule:
    """一条允许的状态流转。"""

    target: HazardStatus
    label: str
    action: RectificationAction
    require_content: bool = False


def _rule(
    target: HazardStatus,
    label: str,
    action: RectificationAction,
    require_content: bool = False,
) -> TransitionRule:
    return TransitionRule(target=target, label=label, action=action, require_content=require_content)


# 隐患整改状态机：待整改 -> 整改中 -> 待验收 -> 已销号（已销号为终态）
TRANSITION_RULES: dict[str, list[TransitionRule]] = {
    HazardStatus.REGISTERED.value: [
        _rule(HazardStatus.RECTIFYING, "开始整改", RectificationAction.MEASURE),
        _rule(
            HazardStatus.CLOSED,
            "直接销号（立行立改）",
            RectificationAction.CLOSE,
            require_content=True,
        ),
    ],
    HazardStatus.RECTIFYING.value: [
        _rule(
            HazardStatus.PENDING_ACCEPTANCE,
            "提交验收",
            RectificationAction.PROGRESS,
            require_content=True,
        ),
        _rule(HazardStatus.CLOSED, "直接销号", RectificationAction.CLOSE, require_content=True),
    ],
    HazardStatus.PENDING_ACCEPTANCE.value: [
        _rule(HazardStatus.CLOSED, "验收通过并销号", RectificationAction.VERIFY),
        _rule(
            HazardStatus.RECTIFYING,
            "验收不通过，退回整改",
            RectificationAction.VERIFY,
            require_content=True,
        ),
    ],
    HazardStatus.CLOSED.value: [],
}


def available_transitions(status: str) -> list[HazardTransitionOption]:
    return [
        HazardTransitionOption(
            target_status=rule.target,
            label=rule.label,
            require_content=rule.require_content,
        )
        for rule in TRANSITION_RULES.get(status, [])
    ]


def _find_rule(current: str, target: str) -> TransitionRule | None:
    for rule in TRANSITION_RULES.get(current, []):
        if rule.target.value == target:
            return rule
    return None


def get_hazard(db: Session, hazard_id: int) -> Hazard:
    stmt = (
        select(Hazard)
        .options(
            selectinload(Hazard.reservoir),
            selectinload(Hazard.rectifications),
        )
        .where(Hazard.id == hazard_id)
    )
    hazard = db.scalar(stmt)
    if hazard is None:
        raise NotFoundError(f"隐患不存在：id={hazard_id}")
    return hazard


def list_hazards(
    db: Session,
    *,
    reservoir_id: int | None = None,
    inspection_id: int | None = None,
    category: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    source: str | None = None,
    keyword: str | None = None,
    overdue_only: bool = False,
    open_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Hazard], int]:
    conditions = []
    if reservoir_id:
        conditions.append(Hazard.reservoir_id == reservoir_id)
    if inspection_id:
        conditions.append(Hazard.inspection_id == inspection_id)
    if category:
        conditions.append(Hazard.category == category)
    if severity:
        conditions.append(Hazard.severity == severity)
    if status:
        conditions.append(Hazard.status == status)
    if source:
        conditions.append(Hazard.source == source)
    if open_only:
        conditions.append(Hazard.status != HazardStatus.CLOSED.value)
    if overdue_only:
        conditions.append(
            and_(
                Hazard.status != HazardStatus.CLOSED.value,
                Hazard.deadline.is_not(None),
                Hazard.deadline < date.today(),
            )
        )
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Hazard.title.like(like),
                Hazard.code.like(like),
                Hazard.description.like(like),
                Hazard.assignee.like(like),
            )
        )

    total = db.scalar(select(func.count()).select_from(Hazard).where(*conditions)) or 0
    rows = db.scalars(
        select(Hazard)
        .options(selectinload(Hazard.reservoir))
        .where(*conditions)
        .order_by(
            Hazard.status.desc(),
            Hazard.deadline.is_(None),
            Hazard.deadline.asc(),
            Hazard.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(rows), total


def create_hazard(db: Session, payload: HazardCreate) -> Hazard:
    reservoir_service.get_reservoir(db, payload.reservoir_id)
    data = enum_to_value(payload.model_dump())

    inspection_id = data.get("inspection_id")
    if inspection_id:
        inspection = db.get(Inspection, inspection_id)
        if inspection is None:
            raise NotFoundError(f"来源巡查记录不存在：id={inspection_id}")
        if inspection.reservoir_id != data["reservoir_id"]:
            raise InvalidOperationError("来源巡查记录与所选水库不一致，请重新选择")

    discovered_on = data.get("discovered_on") or date.today()
    hazard = Hazard(
        code=next_code(db, Hazard, Hazard.code, prefix="YH", on=discovered_on),
        **{**data, "discovered_on": discovered_on},
    )
    # 登记即写一条流水，保证整改跟踪时间轴从发现开始可追溯
    hazard.rectifications.append(
        HazardRectification(
            action=RectificationAction.REGISTER.value,
            content=f"隐患登记：{hazard.title}",
            operator=data.get("discoverer"),
            status_from=None,
            status_to=HazardStatus.REGISTERED.value,
        )
    )
    db.add(hazard)
    db.commit()
    return get_hazard(db, hazard.id)


def update_hazard(db: Session, hazard_id: int, payload: HazardUpdate) -> Hazard:
    hazard = get_hazard(db, hazard_id)

    if payload.status is not None and payload.status.value != hazard.status:
        raise InvalidOperationError(
            "状态变更请使用 POST /api/v1/hazards/{id}/transition 接口，以便记录整改流水"
        )

    data = enum_to_value(payload.model_dump(exclude_unset=True, exclude={"status"}))
    if "inspection_id" in data and data["inspection_id"]:
        inspection = db.get(Inspection, data["inspection_id"])
        if inspection is None:
            raise NotFoundError(f"来源巡查记录不存在：id={data['inspection_id']}")
        if inspection.reservoir_id != hazard.reservoir_id:
            raise InvalidOperationError("来源巡查记录与隐患所属水库不一致")

    for key, value in data.items():
        setattr(hazard, key, value)
    db.commit()
    return get_hazard(db, hazard_id)


def transition_hazard(db: Session, hazard_id: int, payload: HazardTransitionRequest) -> Hazard:
    """按状态机流转隐患状态，并自动写入整改跟踪流水。"""
    hazard = get_hazard(db, hazard_id)
    target = payload.target_status.value
    rule = _find_rule(hazard.status, target)
    if rule is None:
        raise ConflictError(
            f"不允许从「{HazardStatus.label_of(hazard.status)}」变更为"
            f"「{HazardStatus.label_of(target)}」"
        )

    content = (payload.content or "").strip()
    if rule.require_content and not content:
        raise InvalidOperationError(f"变更为「{rule.label}」需要填写处理说明")

    status_from = hazard.status
    hazard.status = target
    hazard.closed_on = date.today() if target == HazardStatus.CLOSED.value else None
    hazard.rectifications.append(
        HazardRectification(
            action=rule.action.value,
            content=content or rule.label,
            operator=payload.operator,
            status_from=status_from,
            status_to=target,
        )
    )
    db.commit()
    return get_hazard(db, hazard_id)


def add_rectification(
    db: Session, hazard_id: int, payload: HazardRectificationCreate
) -> Hazard:
    """追加整改跟踪记录；记录整改措施时自动从「待整改」进入「整改中」。"""
    hazard = get_hazard(db, hazard_id)
    if hazard.status == HazardStatus.CLOSED.value:
        raise ConflictError("隐患已销号，不能再追加整改记录")
    if payload.action in (RectificationAction.REGISTER, RectificationAction.ASSIGN):
        # 登记记录由系统自动生成；指派必须真正修改责任人字段，走批量指派或编辑接口
        raise InvalidOperationError(
            f"「{RectificationAction.label_of(payload.action.value)}」记录不能手工追加，"
            "请使用登记 / 批量指派 / 编辑接口"
        )

    record = HazardRectification(
        action=payload.action.value,
        content=payload.content,
        operator=payload.operator,
        recorded_at=payload.recorded_at or now_local(),
    )
    if (
        payload.action == RectificationAction.MEASURE
        and hazard.status == HazardStatus.REGISTERED.value
    ):
        record.status_from = HazardStatus.REGISTERED.value
        record.status_to = HazardStatus.RECTIFYING.value
        hazard.status = HazardStatus.RECTIFYING.value

    hazard.rectifications.append(record)
    db.commit()
    return get_hazard(db, hazard_id)


def delete_hazard(db: Session, hazard_id: int) -> None:
    hazard = get_hazard(db, hazard_id)
    db.delete(hazard)
    db.commit()


def overdue_hazard_count(db: Session) -> int:
    stmt = (
        select(func.count())
        .select_from(Hazard)
        .where(
            Hazard.status != HazardStatus.CLOSED.value,
            Hazard.deadline.is_not(None),
            Hazard.deadline < date.today(),
        )
    )
    return db.scalar(stmt) or 0


# ---------- 批量操作：指派责任人 / 催办 ----------
#
# 两条铁律：
# 1. 整批原子——批次里任一隐患不满足条件，整批不生效（单事务，校验失败即抛错回滚），
#    并通过 failures 逐条说明原因，绝不出现"只改了一半"。
# 2. 幂等——客户端为每批生成 batch_id，同一 batch_id 重复提交（双击 / 重试 / 刷新重发）
#    直接返回首次处理结果，不会重复写整改流水。


def _dedupe_ids(hazard_ids: list[int]) -> list[int]:
    """去重保序：跨页勾选可能带回重复 id。"""
    return list(dict.fromkeys(hazard_ids))


def _find_batch_records(db: Session, batch_id: str) -> list[HazardRectification]:
    stmt = select(HazardRectification).where(HazardRectification.batch_id == batch_id)
    return list(db.scalars(stmt).all())


def _load_batch_hazards(
    db: Session, hazard_ids: list[int]
) -> tuple[list[Hazard], list[BatchFailureItem]]:
    """按 id 取隐患并保持传入顺序；已不存在的转成失败项。"""
    ids = _dedupe_ids(hazard_ids)
    rows = db.scalars(select(Hazard).where(Hazard.id.in_(ids))).all()
    by_id = {hazard.id: hazard for hazard in rows}
    hazards: list[Hazard] = []
    failures: list[BatchFailureItem] = []
    for hid in ids:
        hazard = by_id.get(hid)
        if hazard is None:
            failures.append(
                BatchFailureItem(
                    hazard_id=hid, reason="隐患不存在或已被删除，请刷新列表后重新勾选"
                )
            )
        else:
            hazards.append(hazard)
    return hazards, failures


def _raise_batch_failure(action_label: str, failures: list[BatchFailureItem]) -> None:
    """任一不满足条件即整批取消：错误说明 + failures 逐条原因一起返回。"""
    preview = "；".join(f"「{item.code or item.hazard_id}」{item.reason}" for item in failures[:5])
    more = f" 等 {len(failures)} 条" if len(failures) > 5 else ""
    raise ConflictError(
        f"批量{action_label}未生效：{len(failures)} 条不满足条件，整批已取消（{preview}{more}）",
        payload={"failures": [item.model_dump() for item in failures]},
    )


def _batch_already_processed(
    payload_batch_id: str, action: RectificationAction, requested: int, existing: list[HazardRectification]
) -> HazardBatchResult:
    """同一批次重复提交：返回首次处理结果，明确标记未重复写入。"""
    return HazardBatchResult(
        batch_id=payload_batch_id,
        action=action,
        requested=requested,
        processed=len(existing),
        already_processed=True,
        processed_ids=sorted(record.hazard_id for record in existing),
    )


def batch_assign(db: Session, payload: HazardBatchAssignRequest) -> HazardBatchResult:
    """批量指派整改责任人，逐条写「指派责任人」流水。"""
    ids = _dedupe_ids(payload.hazard_ids)
    existing = _find_batch_records(db, payload.batch_id)
    if existing:
        return _batch_already_processed(payload.batch_id, RectificationAction.ASSIGN, len(ids), existing)

    assignee = payload.assignee.strip()
    hazards, failures = _load_batch_hazards(db, ids)
    for hazard in hazards:
        if hazard.status == HazardStatus.CLOSED.value:
            failures.append(
                BatchFailureItem(
                    hazard_id=hazard.id,
                    code=hazard.code,
                    title=hazard.title,
                    reason="已销号，无需再指派整改责任人",
                )
            )
        elif (hazard.assignee or "") == assignee:
            failures.append(
                BatchFailureItem(
                    hazard_id=hazard.id,
                    code=hazard.code,
                    title=hazard.title,
                    reason=f"整改责任人已是「{assignee}」，无需重复指派",
                )
            )
    if failures:
        _raise_batch_failure("指派", failures)

    for hazard in hazards:
        previous = hazard.assignee or "未指派"
        hazard.assignee = assignee
        hazard.rectifications.append(
            HazardRectification(
                action=RectificationAction.ASSIGN.value,
                content=f"批量指派整改责任人：{previous} → {assignee}",
                operator=payload.operator,
                batch_id=payload.batch_id,
            )
        )
    db.commit()
    return HazardBatchResult(
        batch_id=payload.batch_id,
        action=RectificationAction.ASSIGN,
        requested=len(ids),
        processed=len(hazards),
        processed_ids=[hazard.id for hazard in hazards],
    )


def batch_urge(db: Session, payload: HazardBatchUrgeRequest) -> HazardBatchResult:
    """批量催办：只写「催办提醒」流水，不变更整改状态。"""
    ids = _dedupe_ids(payload.hazard_ids)
    existing = _find_batch_records(db, payload.batch_id)
    if existing:
        return _batch_already_processed(payload.batch_id, RectificationAction.URGE, len(ids), existing)

    hazards, failures = _load_batch_hazards(db, ids)
    for hazard in hazards:
        if hazard.status == HazardStatus.CLOSED.value:
            failures.append(
                BatchFailureItem(
                    hazard_id=hazard.id,
                    code=hazard.code,
                    title=hazard.title,
                    reason="已销号，无需催办",
                )
            )
        elif not hazard.assignee:
            failures.append(
                BatchFailureItem(
                    hazard_id=hazard.id,
                    code=hazard.code,
                    title=hazard.title,
                    reason="尚未指派整改责任人，请先批量指派",
                )
            )
    if failures:
        _raise_batch_failure("催办", failures)

    note = (payload.content or "").strip()
    for hazard in hazards:
        content = note or f"请责任人「{hazard.assignee}」尽快落实整改并反馈进展"
        hazard.rectifications.append(
            HazardRectification(
                action=RectificationAction.URGE.value,
                content=content,
                operator=payload.operator,
                batch_id=payload.batch_id,
            )
        )
    db.commit()
    return HazardBatchResult(
        batch_id=payload.batch_id,
        action=RectificationAction.URGE,
        requested=len(ids),
        processed=len(hazards),
        processed_ids=[hazard.id for hazard in hazards],
    )


def list_assignee_candidates(
    db: Session, keyword: str | None = None, limit: int = 50
) -> list[AssigneeOption]:
    """整改责任人候选：从历史隐患聚合，按名下未销号数量排序，支持模糊检索。

    候选人可能很多，统一走后端检索 + limit，前端不做全量加载。
    """
    open_count = func.sum(case((Hazard.status != HazardStatus.CLOSED.value, 1), else_=0))
    conditions = [Hazard.assignee.is_not(None), Hazard.assignee != ""]
    if keyword and keyword.strip():
        conditions.append(Hazard.assignee.like(f"%{keyword.strip()}%"))
    stmt = (
        select(Hazard.assignee, open_count.label("open_count"))
        .where(*conditions)
        .group_by(Hazard.assignee)
        .order_by(open_count.desc(), Hazard.assignee.asc())
        .limit(limit)
    )
    return [
        AssigneeOption(name=name, open_count=count or 0)
        for name, count in db.execute(stmt).all()
    ]
