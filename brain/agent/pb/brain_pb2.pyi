from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ToolRequest(_message.Message):
    __slots__ = ("request_id", "obo_token", "name", "args_json")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    OBO_TOKEN_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    ARGS_JSON_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    obo_token: str
    name: str
    args_json: str
    def __init__(
        self,
        request_id: _Optional[str] = ...,
        obo_token: _Optional[str] = ...,
        name: _Optional[str] = ...,
        args_json: _Optional[str] = ...,
    ) -> None: ...

class ToolResponse(_message.Message):
    __slots__ = ("result_json", "ok")
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    OK_FIELD_NUMBER: _ClassVar[int]
    result_json: str
    ok: bool
    def __init__(self, result_json: _Optional[str] = ..., ok: _Optional[bool] = ...) -> None: ...

class Claims(_message.Message):
    __slots__ = ("sub", "name", "role", "dept", "spend_limit", "approval_limit")
    SUB_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    DEPT_FIELD_NUMBER: _ClassVar[int]
    SPEND_LIMIT_FIELD_NUMBER: _ClassVar[int]
    APPROVAL_LIMIT_FIELD_NUMBER: _ClassVar[int]
    sub: str
    name: str
    role: str
    dept: str
    spend_limit: float
    approval_limit: float
    def __init__(
        self,
        sub: _Optional[str] = ...,
        name: _Optional[str] = ...,
        role: _Optional[str] = ...,
        dept: _Optional[str] = ...,
        spend_limit: _Optional[float] = ...,
        approval_limit: _Optional[float] = ...,
    ) -> None: ...

class Turn(_message.Message):
    __slots__ = ("role", "content")
    ROLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    role: str
    content: str
    def __init__(self, role: _Optional[str] = ..., content: _Optional[str] = ...) -> None: ...

class AgentRequest(_message.Message):
    __slots__ = (
        "request_id",
        "claims",
        "scope_hash",
        "masked_query",
        "history",
        "model_tier",
        "obo_token",
        "intent",
    )
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CLAIMS_FIELD_NUMBER: _ClassVar[int]
    SCOPE_HASH_FIELD_NUMBER: _ClassVar[int]
    MASKED_QUERY_FIELD_NUMBER: _ClassVar[int]
    HISTORY_FIELD_NUMBER: _ClassVar[int]
    MODEL_TIER_FIELD_NUMBER: _ClassVar[int]
    OBO_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INTENT_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    claims: Claims
    scope_hash: str
    masked_query: str
    history: _containers.RepeatedCompositeFieldContainer[Turn]
    model_tier: str
    obo_token: str
    intent: str
    def __init__(
        self,
        request_id: _Optional[str] = ...,
        claims: _Optional[_Union[Claims, _Mapping]] = ...,
        scope_hash: _Optional[str] = ...,
        masked_query: _Optional[str] = ...,
        history: _Optional[_Iterable[_Union[Turn, _Mapping]]] = ...,
        model_tier: _Optional[str] = ...,
        obo_token: _Optional[str] = ...,
        intent: _Optional[str] = ...,
    ) -> None: ...

class ConfirmRequest(_message.Message):
    __slots__ = ("request_id", "claims", "proposal_id", "confirmation_token", "obo_token")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CLAIMS_FIELD_NUMBER: _ClassVar[int]
    PROPOSAL_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIRMATION_TOKEN_FIELD_NUMBER: _ClassVar[int]
    OBO_TOKEN_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    claims: Claims
    proposal_id: str
    confirmation_token: str
    obo_token: str
    def __init__(
        self,
        request_id: _Optional[str] = ...,
        claims: _Optional[_Union[Claims, _Mapping]] = ...,
        proposal_id: _Optional[str] = ...,
        confirmation_token: _Optional[str] = ...,
        obo_token: _Optional[str] = ...,
    ) -> None: ...

class AgentEvent(_message.Message):
    __slots__ = ("token", "tool_started", "tool_result", "proposal", "final", "error")
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    TOOL_STARTED_FIELD_NUMBER: _ClassVar[int]
    TOOL_RESULT_FIELD_NUMBER: _ClassVar[int]
    PROPOSAL_FIELD_NUMBER: _ClassVar[int]
    FINAL_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    token: Token
    tool_started: ToolStarted
    tool_result: ToolResult
    proposal: Proposal
    final: Final
    error: Error
    def __init__(
        self,
        token: _Optional[_Union[Token, _Mapping]] = ...,
        tool_started: _Optional[_Union[ToolStarted, _Mapping]] = ...,
        tool_result: _Optional[_Union[ToolResult, _Mapping]] = ...,
        proposal: _Optional[_Union[Proposal, _Mapping]] = ...,
        final: _Optional[_Union[Final, _Mapping]] = ...,
        error: _Optional[_Union[Error, _Mapping]] = ...,
    ) -> None: ...

class Token(_message.Message):
    __slots__ = ("text",)
    TEXT_FIELD_NUMBER: _ClassVar[int]
    text: str
    def __init__(self, text: _Optional[str] = ...) -> None: ...

class ToolStarted(_message.Message):
    __slots__ = ("name", "args_json")
    NAME_FIELD_NUMBER: _ClassVar[int]
    ARGS_JSON_FIELD_NUMBER: _ClassVar[int]
    name: str
    args_json: str
    def __init__(self, name: _Optional[str] = ..., args_json: _Optional[str] = ...) -> None: ...

class ToolResult(_message.Message):
    __slots__ = ("name", "result_json", "ok")
    NAME_FIELD_NUMBER: _ClassVar[int]
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    OK_FIELD_NUMBER: _ClassVar[int]
    name: str
    result_json: str
    ok: bool
    def __init__(
        self, name: _Optional[str] = ..., result_json: _Optional[str] = ..., ok: _Optional[bool] = ...
    ) -> None: ...

class Proposal(_message.Message):
    __slots__ = ("proposal_id", "action", "summary_json")
    PROPOSAL_ID_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_JSON_FIELD_NUMBER: _ClassVar[int]
    proposal_id: str
    action: str
    summary_json: str
    def __init__(
        self,
        proposal_id: _Optional[str] = ...,
        action: _Optional[str] = ...,
        summary_json: _Optional[str] = ...,
    ) -> None: ...

class Final(_message.Message):
    __slots__ = ("text", "status")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    text: str
    status: str
    def __init__(self, text: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class Error(_message.Message):
    __slots__ = ("message",)
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    message: str
    def __init__(self, message: _Optional[str] = ...) -> None: ...
