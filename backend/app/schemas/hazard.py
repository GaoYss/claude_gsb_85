"""隐患登记与整改跟踪 Schema。"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.enums import (
    HazardSeverity,
    HazardSource,
    HazardStatus,
    RectificationAction,
    StructurePart,
)
from app.schemas.common import is_overdue
from app.schemas.reservoir import ReservoirBrief


class HazardCreate(BaseModel):
    reservoir_id: int
    inspection_id: int | None = Field(default=None, description="来源巡查记录，可空")
    title: str = Field(min_length=1, max_length=160, description="隐患标题")
    category: StructurePart = Field(description="隐患类别（部位）")
    severity: HazardSeverity = Field(default=HazardSeverity.GENERAL)
    source: HazardSource = Field(default=HazardSource.INSPECTION)
    discovered_on: date | None = Field(default=None, description="发现日期，缺省为今天")
    discoverer: str | None = Field(default=None, max_length=64)
    deadline: date | None = Field(default=None, description="整改期限")
    assignee: str | None = Field(default=None, max_length=64, description="整改责任人")
    description: str | None = Field(default=None, description="隐患描述")
    plan: str | None = Field(default=None, description="整改方案 / 要求")


class HazardUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    category: StructurePart | None = None
    severity: HazardSeverity | None = None
    source: HazardSource | None = None
    discovered_on: date | None = None
    discoverer: str | None = Field(default=None, max_length=64)
    deadline: date | None = None
    assignee: str | None = Field(default=None, max_length=64)
    description: str | None = None
    plan: str | None = None
    inspection_id: int | None = None
    status: HazardStatus | None = Field(
        default=None, description="如需变更状态，请走 /transition 接口以留痕"
    )


class HazardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    reservoir_id: int
    inspection_id: int | None = None
    title: str
    category: StructurePart
    severity: HazardSeverity
    status: HazardStatus
    source: HazardSource
    discovered_on: date
    discoverer: str | None = None
    deadline: date | None = None
    assignee: str | None = None
    description: str | None = None
    plan: str | None = None
    closed_on: date | None = None
    created_at: datetime
    updated_at: datetime

    reservoir: ReservoirBrief | None = None

    @computed_field(description="是否逾期（未销号且已过整改期限）")
    @property
    def is_overdue(self) -> bool:
        return is_overdue(self.deadline, self.status.value)


class HazardRectificationCreate(BaseModel):
    action: RectificationAction = Field(default=RectificationAction.PROGRESS, description="记录类型")
    content: str = Field(min_length=1, description="记录内容")
    operator: str | None = Field(default=None, max_length=64, description="记录人")
    recorded_at: datetime | None = Field(default=None, description="记录时间，缺省为当前时间")


class HazardRectificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hazard_id: int
    action: RectificationAction
    content: str
    operator: str | None = None
    status_from: HazardStatus | None = None
    status_to: HazardStatus | None = None
    recorded_at: datetime


class HazardTransitionRequest(BaseModel):
    """状态流转请求。"""

    target_status: HazardStatus
    content: str | None = Field(default=None, description="流转说明，会写入整改跟踪流水")
    operator: str | None = Field(default=None, max_length=64, description="操作人")


class HazardTransitionOption(BaseModel):
    """可执行的状态流转（前端据此渲染按钮）。"""

    target_status: HazardStatus
    label: str
    require_content: bool = False


class HazardDetail(HazardRead):
    """隐患详情：附带整改跟踪流水。"""

    rectifications: list[HazardRectificationRead] = Field(default_factory=list)


# ---------- 批量操作（指派 / 催办） ----------

# 单批上限：防止误操作把整库隐患一次改掉，也控制单事务大小
BATCH_MAX_IDS = 200


class HazardBatchAssignRequest(BaseModel):
    """批量指派整改责任人。"""

    hazard_ids: list[int] = Field(min_length=1, max_length=BATCH_MAX_IDS)
    assignee: str = Field(min_length=1, max_length=64, description="整改责任人")
    operator: str | None = Field(default=None, max_length=64, description="操作人")
    batch_id: str = Field(
        min_length=8,
        max_length=64,
        description="客户端生成的幂等键：同一批次重复提交不会产生重复记录",
    )


class HazardBatchUrgeRequest(BaseModel):
    """批量催办。"""

    hazard_ids: list[int] = Field(min_length=1, max_length=BATCH_MAX_IDS)
    content: str | None = Field(default=None, max_length=200, description="催办说明，可空")
    operator: str | None = Field(default=None, max_length=64, description="操作人")
    batch_id: str = Field(min_length=8, max_length=64, description="幂等键，同批量指派")


class BatchFailureItem(BaseModel):
    """批量操作中单条不满足条件的明细。"""

    hazard_id: int
    code: str = ""
    title: str = ""
    reason: str


class HazardBatchResult(BaseModel):
    """批量操作结果。requested 与 processed 一致才说明勾选数量全部处理。"""

    batch_id: str
    action: RectificationAction
    requested: int = Field(description="请求条数（去重后）")
    processed: int = Field(description="实际处理条数")
    already_processed: bool = Field(
        default=False, description="该批次此前已成功处理，本次为重复提交，未重复写入"
    )
    processed_ids: list[int] = Field(default_factory=list)


class AssigneeOption(BaseModel):
    """整改责任人候选（从历史隐患中聚合）。"""

    name: str
    open_count: int = Field(description="该责任人名下未销号隐患数")

