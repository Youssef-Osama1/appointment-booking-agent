class LLMOutputError(Exception):
    pass


class ToolError(Exception):
    pass


class AgentStepLimitError(Exception):
    pass


class PatientNotFoundError(Exception):
    pass


class ConversationNotFoundError(Exception):
    pass


class ActionNotFoundError(Exception):
    pass


class ActionExpiredError(Exception):
    pass


class ActionConflictError(Exception):
    pass
