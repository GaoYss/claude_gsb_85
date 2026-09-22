"""隐患登记与整改跟踪接口。"""

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, PageParams
from app.models.enums import (
    HazardSeverity,
    HazardSource,
    HazardStatus,
    StructurePart,
)
from app.schemas.common import Message, Page
from app.schemas.hazard import (
    AssigneeOption,
    HazardBatchAssignRequest,
    HazardBatchResult,
    HazardBatchUrgeRequest,
    HazardCreate,
    HazardDetail,
    HazardRead,
    HazardRectificationCreate,
    HazardTransitionRequest,
    HazardUpdate,
)
from app.services import hazard_service

router = APIRouter(prefix="/hazards", tags=["隐患与整改"])


@router.get("", response_model=Page[HazardRead], summary="分页查询隐患台账")
def list_hazards(
    db: DbSession,
    pagination: PageParams,
    reservoir_id: int | None = Query(default=None, description="按水库过滤"),
    inspection_id: int | None = Query(default=None, description="按来源巡查记录过滤"),
    category: StructurePart | None = Query(default=None, description="隐患类别（部位）"),
    severity: HazardSeverity | None = Query(default=None, description="隐患等级"),
    hazard_status: HazardStatus | None = Query(default=None, alias="status", description="整改状态"),
    source: HazardSource | None = Query(default=None, description="隐患来源"),
    keyword: str | None = Query(default=None, description="按标题 / 编号 / 描述 / 责任人搜索"),
    open_only: bool = Query(default=False, description="只看未销号隐患"),
    overdue_only: bool = Query(default=False, description="只看逾期未整改隐患"),
) -> Page[HazardRead]:
    items, total = hazard_service.list_hazards(
        db,
        reservoir_id=reservoir_id,
        inspection_id=inspection_id,
        category=category.value if category else None,
        severity=severity.value if severity else None,
        status=hazard_status.value if hazard_status else None,
        source=source.value if source else None,
        keyword=keyword,
        open_only=open_only,
        overdue_only=overdue_only,
        page=pagination.page,
        page_size=pagination.page_size,
    )
    return Page.build(items=items, total=total, page=pagination.page, page_size=pagination.page_size)


@router.post(
    "",
    response_model=HazardDetail,
    status_code=status.HTTP_201_CREATED,
    summary="登记隐患",
)
def create_hazard(payload: HazardCreate, db: DbSession) -> HazardDetail:
    return hazard_service.create_hazard(db, payload)


# 注意：/batch-assign、/batch-urge、/assignees 必须注册在 /{hazard_id} 之前，
# 否则 "batch-assign" 会被当作 {hazard_id} 匹配并因 int 校验失败返回 422。


@router.get(
    "/assignees",
    response_model=list[AssigneeOption],
    summary="整改责任人候选（按名下未销号数量排序，支持检索）",
)
def list_assignees(
    db: DbSession,
    keyword: str | None = Query(default=None, description="按姓名模糊检索"),
    limit: int = Query(default=50, ge=1, le=200, description="返回条数上限"),
) -> list[AssigneeOption]:
    return hazard_service.list_assignee_candidates(db, keyword=keyword, limit=limit)


@router.post(
    "/batch-assign",
    response_model=HazardBatchResult,
    summary="批量指派整改责任人（整批原子 + 幂等）",
    responses={409: {"description": "存在不满足条件的隐患，整批未生效，failures 逐条说明"}},
)
def batch_assign(payload: HazardBatchAssignRequest, db: DbSession) -> HazardBatchResult:
    return hazard_service.batch_assign(db, payload)


@router.post(
    "/batch-urge",
    response_model=HazardBatchResult,
    summary="批量催办（整批原子 + 幂等）",
    responses={409: {"description": "存在不满足条件的隐患，整批未生效，failures 逐条说明"}},
)
def batch_urge(payload: HazardBatchUrgeRequest, db: DbSession) -> HazardBatchResult:
    return hazard_service.batch_urge(db, payload)


@router.get("/{hazard_id}", response_model=HazardDetail, summary="隐患详情（含整改流水）")
def get_hazard(hazard_id: int, db: DbSession) -> HazardDetail:
    return hazard_service.get_hazard(db, hazard_id)


@router.put("/{hazard_id}", response_model=HazardDetail, summary="更新隐患信息")
def update_hazard(hazard_id: int, payload: HazardUpdate, db: DbSession) -> HazardDetail:
    return hazard_service.update_hazard(db, hazard_id, payload)


@router.post(
    "/{hazard_id}/rectifications",
    response_model=HazardDetail,
    status_code=status.HTTP_201_CREATED,
    summary="追加整改跟踪记录",
)
def add_rectification(
    hazard_id: int, payload: HazardRectificationCreate, db: DbSession
) -> HazardDetail:
    return hazard_service.add_rectification(db, hazard_id, payload)


@router.post(
    "/{hazard_id}/transition",
    response_model=HazardDetail,
    summary="整改状态流转（开始整改 / 提交验收 / 销号 / 退回整改）",
    responses={409: {"description": "当前状态不允许该流转"}},
)
def transition_hazard(
    hazard_id: int, payload: HazardTransitionRequest, db: DbSession
) -> HazardDetail:
    return hazard_service.transition_hazard(db, hazard_id, payload)


@router.delete("/{hazard_id}", response_model=Message, summary="删除隐患及其整改流水")
def delete_hazard(hazard_id: int, db: DbSession) -> Message:
    hazard_service.delete_hazard(db, hazard_id)
    return Message(detail="隐患已删除", code="deleted")

