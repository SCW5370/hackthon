"""AttackLab — AttackCase 加载与 Schema 验证."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

CASE_DIR = Path(__file__).parents[1] / "contracts" / "examples"

# 动态 import jsonschema，只在首次验证时加载
_jsonschema = None

def _get_validator():
    global _jsonschema
    if _jsonschema is None:
        import jsonschema
        from jsonschema import Draft202012Validator
        # 预加载所有 schema 供 AttackCase.from_dict 使用
        schemas = {}
        schema_dir = Path(__file__).parents[1] / "contracts"
        for path in schema_dir.glob("*.schema.json"):
            with open(path) as f:
                schema = json.load(f)
            schemas[path.stem] = Draft202012Validator(schema)
        _jsonschema = schemas
    return _jsonschema


class ValidationError(Exception):
    """Schema 校验失败"""


@dataclass
class AttackCase:
    """一个攻击用例的内存表示。"""
    schema_version: str
    attack_id: str
    title: str
    channel: str
    target_task_id: str
    operator_task: str
    untrusted_content: str
    actor_claims: dict
    expected_agent_intent: dict
    expected_safeexec: dict
    expected_recovery: dict
    # 来源文件（可选）
    source_file: str | None = None
    # 内部生成
    _run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _run_at_ms: int = 0

    @classmethod
    def from_dict(cls, raw: dict, source_file: str | None = None) -> "AttackCase":
        validators = _get_validator()
        schema = None
        # 根据 schema_version 找对应 schema
        for key, validator in validators.items():
            if key.startswith("attack_case"):
                try:
                    validator.validate(raw)
                    schema = key
                except Exception:
                    pass
        if schema is None:
            raise ValidationError(
                f"无法匹配任何 AttackCase schema: {raw.get('attack_id', 'unknown')}"
            )

        return cls(
            schema_version=raw["schema_version"],
            attack_id=raw["attack_id"],
            title=raw["title"],
            channel=raw["channel"],
            target_task_id=raw["target_task_id"],
            operator_task=raw["operator_task"],
            untrusted_content=raw.get("untrusted_content", ""),
            actor_claims=raw.get("actor_claims", {}),
            expected_agent_intent=raw["expected_agent_intent"],
            expected_safeexec=raw["expected_safeexec"],
            expected_recovery=raw.get("expected_recovery", {}),
            source_file=source_file,
        )

    @classmethod
    def from_json_file(cls, path: Path) -> "AttackCase":
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return cls.from_dict(raw, source_file=str(path))

    @classmethod
    def list_cases(cls) -> list["AttackCase"]:
        """加载所有 attack_case JSON 示例文件。"""
        case_dir = Path(__file__).parents[1] / "contracts" / "examples"
        cases = []
        for p in sorted(case_dir.glob("attack_case.*.json")):
            try:
                cases.append(cls.from_json_file(p))
            except Exception as exc:
                print(f"WARNING: skipped {p.name}: {exc}")
        return cases

    @classmethod
    def find(cls, attack_id: str) -> "AttackCase | None":
        """按 attack_id 查找用例。"""
        for c in cls.list_cases():
            if c.attack_id == attack_id:
                return c
        return None
