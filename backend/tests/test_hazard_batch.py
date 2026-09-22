"""隐患批量操作（指派 / 催办）接口测试。

覆盖需求的四条硬约束：
- 每次指派 / 催办都写整改流水；
- 批次里任一条不满足条件 → 整批不生效，逐条说明原因，不出现"只改一半"；
- 同一批次重复提交（同 batch_id）不产生重复记录；
- 请求条数与实际处理条数一致（含跨页勾选带重复 id 的场景）。
"""

import uuid

from tests.conftest import API


def _batch_id() -> str:
    return uuid.uuid4().hex


def _create_hazard(client, reservoir_id: int, **overrides) -> dict:
    payload = {
        "reservoir_id": reservoir_id,
        "title": "坝体局部裂缝",
        "category": "dam_body",
        "severity": "general",
        "source": "inspection",
    }
    payload.update(overrides)
    response = client.post(f"{API}/hazards", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _close_hazard(client, hazard_id: int) -> None:
    response = client.post(
        f"{API}/hazards/{hazard_id}/transition",
        json={"target_status": "closed", "content": "立行立改"},
    )
    assert response.status_code == 200, response.text


def _actions_of(client, hazard_id: int) -> list[str]:
    detail = client.get(f"{API}/hazards/{hazard_id}").json()
    return [record["action"] for record in detail["rectifications"]]


def test_batch_assign_writes_records_and_keeps_status(client, make_reservoir):
    reservoir = make_reservoir()
    first = _create_hazard(client, reservoir["id"], title="隐患一")
    second = _create_hazard(client, reservoir["id"], title="隐患二", assignee="张三")

    response = client.post(
        f"{API}/hazards/batch-assign",
        json={
            "hazard_ids": [first["id"], second["id"]],
            "assignee": "李四",
            "operator": "王五",
            "batch_id": _batch_id(),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requested"] == 2
    assert body["processed"] == 2
    assert body["already_processed"] is False
    assert sorted(body["processed_ids"]) == sorted([first["id"], second["id"]])

    for hazard_id, expect_previous in [(first["id"], "未指派"), (second["id"], "张三")]:
        detail = client.get(f"{API}/hazards/{hazard_id}").json()
        assert detail["assignee"] == "李四"
        assert detail["status"] == "registered"  # 指派不改状态
        record = detail["rectifications"][-1]
        assert record["action"] == "assign"
        assert record["operator"] == "王五"
        assert expect_previous in record["content"]
        assert "李四" in record["content"]


def test_batch_urge_writes_records_with_default_content(client, make_reservoir):
    reservoir = make_reservoir()
    hazard = _create_hazard(client, reservoir["id"], assignee="张三")

    response = client.post(
        f"{API}/hazards/batch-urge",
        json={"hazard_ids": [hazard["id"]], "batch_id": _batch_id()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["processed"] == 1

    detail = client.get(f"{API}/hazards/{hazard['id']}").json()
    record = detail["rectifications"][-1]
    assert record["action"] == "urge"
    assert "张三" in record["content"]
    assert detail["status"] == "registered"  # 催办不改状态


def test_batch_assign_is_atomic_when_any_item_invalid(client, make_reservoir):
    """一条不满足 → 整批不生效：其余隐患的责任人和流水都不能变。"""
    reservoir = make_reservoir()
    ok = _create_hazard(client, reservoir["id"], title="正常隐患")
    closed = _create_hazard(client, reservoir["id"], title="已销号隐患")
    _close_hazard(client, closed["id"])

    response = client.post(
        f"{API}/hazards/batch-assign",
        json={
            "hazard_ids": [ok["id"], closed["id"], 999999],
            "assignee": "李四",
            "batch_id": _batch_id(),
        },
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert "整批已取消" in body["detail"]

    failures = {item["hazard_id"]: item["reason"] for item in body["failures"]}
    assert len(failures) == 2
    assert "已销号" in failures[closed["id"]]
    assert "不存在" in failures[999999]

    # 整批回滚：正常隐患未被修改，也没有新增流水
    detail = client.get(f"{API}/hazards/{ok['id']}").json()
    assert detail["assignee"] is None
    assert _actions_of(client, ok["id"]) == ["register"]


def test_batch_assign_rejects_unchanged_assignee(client, make_reservoir):
    reservoir = make_reservoir()
    hazard = _create_hazard(client, reservoir["id"], assignee="张三")

    response = client.post(
        f"{API}/hazards/batch-assign",
        json={"hazard_ids": [hazard["id"]], "assignee": "张三", "batch_id": _batch_id()},
    )
    assert response.status_code == 409
    assert "无需重复指派" in response.json()["failures"][0]["reason"]


def test_batch_urge_requires_assignee_and_open_status(client, make_reservoir):
    reservoir = make_reservoir()
    no_assignee = _create_hazard(client, reservoir["id"], title="未指派")
    closed = _create_hazard(client, reservoir["id"], title="已销号", assignee="张三")
    _close_hazard(client, closed["id"])

    response = client.post(
        f"{API}/hazards/batch-urge",
        json={"hazard_ids": [no_assignee["id"], closed["id"]], "batch_id": _batch_id()},
    )
    assert response.status_code == 409
    reasons = {item["hazard_id"]: item["reason"] for item in response.json()["failures"]}
    assert "尚未指派整改责任人" in reasons[no_assignee["id"]]
    assert "已销号" in reasons[closed["id"]]

    # 整批未生效：两条隐患都不应有催办记录
    assert _actions_of(client, no_assignee["id"]) == ["register"]


def test_same_batch_id_is_idempotent(client, make_reservoir):
    """同一批次重复提交（双击 / 重试）：不重复指派、不重复写流水。"""
    reservoir = make_reservoir()
    first = _create_hazard(client, reservoir["id"], title="隐患一")
    second = _create_hazard(client, reservoir["id"], title="隐患二")
    batch_id = _batch_id()
    payload = {"hazard_ids": [first["id"], second["id"]], "assignee": "李四", "batch_id": batch_id}

    first_response = client.post(f"{API}/hazards/batch-assign", json=payload)
    assert first_response.status_code == 200
    assert first_response.json()["already_processed"] is False

    replay = client.post(f"{API}/hazards/batch-assign", json=payload)
    assert replay.status_code == 200, replay.text
    body = replay.json()
    assert body["already_processed"] is True
    assert body["processed"] == 2

    for hazard_id in (first["id"], second["id"]):
        # 流水里只应有一条 assign（即首次提交写入的那条）
        assert _actions_of(client, hazard_id) == ["register", "assign"]

    # 催办同理：重复提交不产生重复催办记录
    urge_payload = {"hazard_ids": [first["id"]], "batch_id": _batch_id()}
    assert client.post(f"{API}/hazards/batch-urge", json=urge_payload).status_code == 200
    replayed = client.post(f"{API}/hazards/batch-urge", json=urge_payload).json()
    assert replayed["already_processed"] is True
    assert _actions_of(client, first["id"]) == ["register", "assign", "urge"]


def test_batch_ids_are_deduplicated(client, make_reservoir):
    """跨页勾选可能带回重复 id：去重后处理，数量对得上。"""
    reservoir = make_reservoir()
    hazard = _create_hazard(client, reservoir["id"])

    response = client.post(
        f"{API}/hazards/batch-assign",
        json={
            "hazard_ids": [hazard["id"], hazard["id"], hazard["id"]],
            "assignee": "李四",
            "batch_id": _batch_id(),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["requested"] == 1
    assert body["processed"] == 1
    assert _actions_of(client, hazard["id"]) == ["register", "assign"]


def test_assignee_candidates_aggregation_and_search(client, make_reservoir):
    reservoir = make_reservoir()
    _create_hazard(client, reservoir["id"], assignee="张三")
    _create_hazard(client, reservoir["id"], assignee="张三")
    closed = _create_hazard(client, reservoir["id"], assignee="张三")
    _close_hazard(client, closed["id"])
    _create_hazard(client, reservoir["id"], assignee="李四")

    candidates = client.get(f"{API}/hazards/assignees").json()
    by_name = {item["name"]: item["open_count"] for item in candidates}
    assert by_name["张三"] == 2  # 已销号的不计入未销号数
    assert by_name["李四"] == 1
    # 按未销号数量降序
    assert candidates[0]["name"] == "张三"

    searched = client.get(f"{API}/hazards/assignees", params={"keyword": "李"}).json()
    assert [item["name"] for item in searched] == ["李四"]


def test_batch_rejects_empty_ids(client):
    response = client.post(
        f"{API}/hazards/batch-urge", json={"hazard_ids": [], "batch_id": _batch_id()}
    )
    assert response.status_code == 422
