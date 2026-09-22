"""隐患登记与整改跟踪业务逻辑（含状态流转规则）。"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ConflictError, InvalidOperationError, NotFoundError
from app.db.base import now_local
from app.models import Hazard, HazardBatchOperation, HazardRectification, Inspection
from app.models.enums import HazardStatus, RectificationAction
from app.schemas.hazard import (
    HazardBatchAssignRequest,
    HazardBatchRemindRequest,
    HazardBatchRequest,
    HazardBatchResult,
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
    if payload.action in (
        RectificationAction.REGISTER,
        RectificationAction.ASSIGN,
        RectificationAction.REMIND,
    ):
        raise InvalidOperationError(
            f"「{RectificationAction.label_of(payload.action.value)}」记录由系统自动生成"
            "（登记 / 批量操作），不能人工追加"
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


# ---------------------------------------------------------------------------
# 批量操作（批量指派责任人 / 批量催办）
# ---------------------------------------------------------------------------

_BATCH_ACTION_LABELS = {"assign": "批量指派", "remind": "批量催办"}


def _batch_result_from_record(record: HazardBatchOperation) -> HazardBatchResult:
    """从已落库的批次记录还原结果，用于幂等重放。"""
    snapshot = json.loads(record.detail)
    return HazardBatchResult(
        request_id=record.request_id,
        action=record.action,
        processed_count=record.processed_count,
        hazard_ids=snapshot["hazard_ids"],
        already_processed=True,
        message=(
            f"该批次已于 {record.created_at:%Y-%m-%d %H:%M:%S} 处理过，"
            f"本次重复提交已被忽略，未产生新的整改记录"
        ),
    )


def _execute_batch(
    db: Session,
    payload: HazardBatchRequest,
    *,
    action: str,
    apply_hazard: Callable[[Hazard], str],
) -> HazardBatchResult:
    """批量操作公共骨架：整批校验 -> 整批应用 -> 单事务提交。

    - 原子性：任一隐患不满足条件即整批回滚，报文逐条说明原因，不存在"改一半"；
    - 幂等：request_id 唯一，重复提交直接重放首次结果，不产生重复流水；
    - apply_hazard 负责单条隐患的字段变更，并返回写入整改流水的内容。
    """
    label = _BATCH_ACTION_LABELS[action]

    existing = db.scalar(
        select(HazardBatchOperation).where(HazardBatchOperation.request_id == payload.request_id)
    )
    if existing is not None:
        same_batch = (
            existing.action == action
            and json.loads(existing.detail)["hazard_ids"] == sorted(set(payload.hazard_ids))
        )
        if not same_batch:
            raise ConflictError(
                f"request_id「{payload.request_id}」已被另一批{'' if existing.action == action else '不同'}操作占用，"
                "请重新发起批量操作以生成新的批次号"
            )
        return _batch_result_from_record(existing)

    # 去重并保持勾选顺序，避免同一隐患在一批里被处理两次
    hazard_ids = list(dict.fromkeys(payload.hazard_ids))
    hazards = {
        hazard.id: hazard
        for hazard in db.scalars(select(Hazard).where(Hazard.id.in_(hazard_ids))).all()
    }

    failures: list[str] = []
    for hazard_id in hazard_ids:
        hazard = hazards.get(hazard_id)
        if hazard is None:
            failures.append(f"id={hazard_id}：隐患不存在（可能已被删除，请刷新列表后重新勾选）")
        elif hazard.status == HazardStatus.CLOSED.value:
            failures.append(
                f"{hazard.code}「{hazard.title}」：已销号，无需{label.replace('批量', '')}"
            )
    if failures:
        raise ConflictError(
            f"{label}未生效：本批 {len(hazard_ids)} 条均未改动，"
            f"其中 {len(failures)} 条不满足条件：\n" + "\n".join(f"· {item}" for item in failures)
        )

    note = (payload.note or "").strip()
    for hazard_id in hazard_ids:
        hazard = hazards[hazard_id]
        content = apply_hazard(hazard)
        if note:
            content = f"{content}；备注：{note}"
        hazard.rectifications.append(
            HazardRectification(
                action=action,
                content=content,
                operator=payload.operator,
            )
        )

    message = f"{label}完成：共处理 {len(hazard_ids)} 条"
    db.add(
        HazardBatchOperation(
            request_id=payload.request_id,
            action=action,
            operator=payload.operator,
            processed_count=len(hazard_ids),
            detail=json.dumps(
                {
                    "hazard_ids": sorted(hazard_ids),
                    "note": note or None,
                    "assignee": getattr(payload, "assignee", None),
                },
                ensure_ascii=False,
            ),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # 并发下同一个 request_id 已被其他请求抢先落库：回滚后重放首次结果
        db.rollback()
        existing = db.scalar(
            select(HazardBatchOperation).where(
                HazardBatchOperation.request_id == payload.request_id
            )
        )
        if existing is None:  # pragma: no cover - 理论上唯一约束冲突时必然已存在
            raise ConflictError("批量操作提交冲突，请重试")
        return _batch_result_from_record(existing)

    return HazardBatchResult(
        request_id=payload.request_id,
        action=action,
        processed_count=len(hazard_ids),
        hazard_ids=hazard_ids,
        already_processed=False,
        message=message,
    )


def batch_assign(db: Session, payload: HazardBatchAssignRequest) -> HazardBatchResult:
    """批量指派整改责任人：更新 assignee 并逐条写「指派责任人」流水。"""
    assignee = payload.assignee.strip()

    def _apply(hazard: Hazard) -> str:
        previous = hazard.assignee
        hazard.assignee = assignee
        if previous and previous != assignee:
            return f"整改责任人由「{previous}」调整为「{assignee}」（批量指派）"
        return f"整改责任人指派为「{assignee}」（批量指派）"

    return _execute_batch(db, payload, action=RectificationAction.ASSIGN.value, apply_hazard=_apply)


def batch_remind(db: Session, payload: HazardBatchRemindRequest) -> HazardBatchResult:
    """批量催办：不改动状态，逐条写「催办提醒」流水。"""

    def _apply(hazard: Hazard) -> str:
        target = hazard.assignee or "整改责任人"
        return f"催办提醒：请{target}尽快落实整改措施并反馈进展（批量催办）"

    return _execute_batch(db, payload, action=RectificationAction.REMIND.value, apply_hazard=_apply)


def assignee_candidates(db: Session, keyword: str | None = None, limit: int = 20) -> list[str]:
    """整改责任人候选：聚合历史责任人、发现人、巡查人，按使用频次排序。

    候选人可能很多，统一走服务端模糊检索 + 限量返回，前端下拉保持可用。
    """
    counts: dict[str, int] = {}
    for column in (Hazard.assignee, Hazard.discoverer, Inspection.inspector):
        stmt = select(column, func.count()).where(column.is_not(None), column != "")
        if keyword and keyword.strip():
            stmt = stmt.where(column.like(f"%{keyword.strip()}%"))
        for name, count in db.execute(stmt.group_by(column)).all():
            counts[name] = counts.get(name, 0) + count
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [name for name, _ in ordered[:limit]]
