"""隐患批量操作（批量指派 / 批量催办）接口测试。

重点验证：
- 每次指派 / 催办都写入整改流水；
- 批次原子性：任一记录不满足条件则整批不生效，且报文逐条说明原因；
- 幂等：同一 request_id 重复提交不产生重复记录；
- 候选人检索在数据量大时仍可用（服务端过滤 + 限量）。
"""

from tests.conftest import API


def _create_hazard(client, reservoir_id: int, **overrides) -> dict:
    payload = {
        "reservoir_id": reservoir_id,
        "title": "坝体局部裂缝",
        "category": "dam_body",
        "severity": "general",
        "source": "inspection",
        "discoverer": "张三",
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


def _rectification_actions(client, hazard_id: int) -> list[str]:
    detail = client.get(f"{API}/hazards/{hazard_id}").json()
    return [record["action"] for record in detail["rectifications"]]


def test_batch_assign_updates_assignee_and_writes_records(client, make_reservoir):
    reservoir = make_reservoir()
    first = _create_hazard(client, reservoir["id"], title="隐患一")
    second = _create_hazard(client, reservoir["id"], title="隐患二", assignee="旧责任人")

    response = client.post(
        f"{API}/hazards/batch-assign",
        json={
            "hazard_ids": [first["id"], second["id"]],
            "assignee": "王海涛",
            "operator": "管理员",
            "note": "汛期前集中指派",
            "request_id": "batch-assign-0001",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["processed_count"] == 2
    assert body["hazard_ids"] == [first["id"], second["id"]]
    assert body["already_processed"] is False

    # 每条隐患都留下指派流水，且变更前后责任人可追溯
    first_detail = client.get(f"{API}/hazards/{first['id']}").json()
    assert first_detail["assignee"] == "王海涛"
    record = first_detail["rectifications"][-1]
    assert record["action"] == "assign"
    assert record["operator"] == "管理员"
    assert "王海涛" in record["content"] and "汛期前集中指派" in record["content"]

    second_detail = client.get(f"{API}/hazards/{second['id']}").json()
    assert "旧责任人" in second_detail["rectifications"][-1]["content"]


def test_batch_remind_writes_records_without_changing_status(client, make_reservoir):
    reservoir = make_reservoir()
    hazard = _create_hazard(client, reservoir["id"], assignee="李四")

    response = client.post(
        f"{API}/hazards/batch-remind",
        json={
            "hazard_ids": [hazard["id"]],
            "operator": "管理员",
            "request_id": "batch-remind-0001",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["processed_count"] == 1

    detail = client.get(f"{API}/hazards/{hazard['id']}").json()
    assert detail["status"] == "registered"  # 催办不改变整改状态
    record = detail["rectifications"][-1]
    assert record["action"] == "remind"
    assert "李四" in record["content"]


def test_batch_operation_is_atomic_when_any_item_invalid(client, make_reservoir):
    reservoir = make_reservoir()
    ok_hazard = _create_hazard(client, reservoir["id"], title="正常隐患")
    closed = _create_hazard(client, reservoir["id"], title="已销号隐患")
    _close_hazard(client, closed["id"])

    response = client.post(
        f"{API}/hazards/batch-assign",
        json={
            "hazard_ids": [ok_hazard["id"], closed["id"], 999999],
            "assignee": "王海涛",
            "request_id": "batch-assign-atomic",
        },
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    # 明确整批未生效，并逐条说明原因
    assert "整批" in detail or "均未改动" in detail
    assert "已销号" in detail
    assert "999999" in detail

    # 整批回滚：正常隐患的责任人与流水都没有变化
    ok_detail = client.get(f"{API}/hazards/{ok_hazard['id']}").json()
    assert ok_detail["assignee"] is None
    assert _rectification_actions(client, ok_hazard["id"]) == ["register"]


def test_batch_remind_rejects_closed_hazard(client, make_reservoir):
    reservoir = make_reservoir()
    hazard = _create_hazard(client, reservoir["id"])
    _close_hazard(client, hazard["id"])

    response = client.post(
        f"{API}/hazards/batch-remind",
        json={"hazard_ids": [hazard["id"]], "request_id": "batch-remind-closed"},
    )
    assert response.status_code == 409
    assert "已销号" in response.json()["detail"]


def test_same_request_id_replays_without_duplicate_records(client, make_reservoir):
    reservoir = make_reservoir()
    hazard = _create_hazard(client, reservoir["id"])

    payload = {"hazard_ids": [hazard["id"]], "request_id": "batch-remind-idem"}
    first = client.post(f"{API}/hazards/batch-remind", json=payload)
    assert first.status_code == 200
    assert first.json()["already_processed"] is False

    # 同一批重复提交（网络重试 / 双击）：不产生重复催办记录
    second = client.post(f"{API}/hazards/batch-remind", json=payload)
    assert second.status_code == 200
    body = second.json()
    assert body["already_processed"] is True
    assert body["processed_count"] == first.json()["processed_count"]

    assert _rectification_actions(client, hazard["id"]).count("remind") == 1


def test_request_id_cannot_be_reused_for_a_different_batch(client, make_reservoir):
    reservoir = make_reservoir()
    first = _create_hazard(client, reservoir["id"], title="隐患一")
    second = _create_hazard(client, reservoir["id"], title="隐患二")

    ok = client.post(
        f"{API}/hazards/batch-remind",
        json={"hazard_ids": [first["id"]], "request_id": "batch-shared-key"},
    )
    assert ok.status_code == 200

    conflict = client.post(
        f"{API}/hazards/batch-remind",
        json={"hazard_ids": [second["id"]], "request_id": "batch-shared-key"},
    )
    assert conflict.status_code == 409
    assert "request_id" in conflict.json()["detail"]

    other_action = client.post(
        f"{API}/hazards/batch-assign",
        json={
            "hazard_ids": [first["id"]],
            "assignee": "王海涛",
            "request_id": "batch-shared-key",
        },
    )
    assert other_action.status_code == 409


def test_batch_request_validation(client, make_reservoir):
    reservoir = make_reservoir()
    hazard = _create_hazard(client, reservoir["id"])

    empty = client.post(
        f"{API}/hazards/batch-remind",
        json={"hazard_ids": [], "request_id": "batch-empty-ids"},
    )
    assert empty.status_code == 422

    short_key = client.post(
        f"{API}/hazards/batch-remind",
        json={"hazard_ids": [hazard["id"]], "request_id": "abc"},
    )
    assert short_key.status_code == 422

    missing_assignee = client.post(
        f"{API}/hazards/batch-assign",
        json={"hazard_ids": [hazard["id"]], "request_id": "batch-no-assignee"},
    )
    assert missing_assignee.status_code == 422


def test_assign_and_remind_records_cannot_be_added_manually(client, make_reservoir):
    reservoir = make_reservoir()
    hazard = _create_hazard(client, reservoir["id"])

    for action in ("register", "assign", "remind"):
        response = client.post(
            f"{API}/hazards/{hazard['id']}/rectifications",
            json={"action": action, "content": "人工补录"},
        )
        assert response.status_code == 422, action
        assert "不能人工追加" in response.json()["detail"]


def test_assignee_candidates_aggregates_and_searches(client, make_reservoir):
    reservoir = make_reservoir()
    _create_hazard(client, reservoir["id"], assignee="王海涛", discoverer="张三")
    _create_hazard(client, reservoir["id"], title="隐患二", assignee="王海涛", discoverer="李四")
    client.post(f"{API}/inspections", json={"reservoir_id": reservoir["id"], "inspector": "李巡查"})

    candidates = client.get(f"{API}/hazards/assignee-candidates").json()
    assert candidates[0] == "王海涛"  # 出现 2 次，排在最前
    assert set(candidates) == {"王海涛", "张三", "李四", "李巡查"}

    searched = client.get(f"{API}/hazards/assignee-candidates", params={"keyword": "巡查"}).json()
    assert searched == ["李巡查"]

    limited = client.get(f"{API}/hazards/assignee-candidates", params={"limit": 1}).json()
    assert limited == ["王海涛"]
