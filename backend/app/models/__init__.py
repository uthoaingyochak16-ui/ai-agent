from app.models.admin import AdminUser
from app.models.conversation import Conversation, Message, ToolCallLog
from app.models.profile import ChannelIdentity, FamilyMember, Profile, ProfileFact, ProfileMergeLog
from app.models.kb import Chunk, Document, QAPair, SemanticCache, UnansweredQuestion, Variable
from app.models.llm import LLMCall, LLMModel, LLMProviderConfig, LLMRoute
from app.models.system import AppSetting, ChannelConfig, WebhookEvent
from app.models.evaluation import EvalCase, EvalResult, EvalRun
from app.models.program import Program
from app.models.keyword import KeywordEntry, RecordKeywords

__all__ = ["AdminUser", "AppSetting", "ChannelConfig", "Chunk", "Document", "QAPair", "SemanticCache", "UnansweredQuestion", "Variable", "ChannelIdentity", "Conversation", "LLMCall", "LLMModel", "LLMProviderConfig", "LLMRoute", "Message",
           "FamilyMember", "Profile", "ProfileFact", "ProfileMergeLog", "Program", "KeywordEntry", "RecordKeywords", "ToolCallLog", "WebhookEvent", "EvalCase", "EvalResult", "EvalRun"]

from app.models import keyword_hooks  # noqa: E402,F401 - registers the keyword-index flush hook
