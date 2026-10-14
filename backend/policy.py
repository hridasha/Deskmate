"""Which tools each agent may use. Everyone gets the safe base set; riskier tools (web access) are opt-in per agent."""

BASE_TOOLS = {"take_note", "recall_notes", "remember", "recall", "get_current_time", "convert_time", "search_knowledge"}

# Optional tools an agent can be granted, with a human description for the UI.
OPTIONAL_TOOLS = {
    "fetch": "Browse the web (fetch and read a page)",
    "database": "Query connected databases (read-only SQL)",
}

# An optional permission can unlock several tools.
GROUPS = {"database": {"describe_data_sources", "query_database"}}


def allowed_tools(character: dict) -> set[str]:
    extra: set[str] = set()
    for t in character.get("tools", []):
        if t in OPTIONAL_TOOLS:
            extra |= GROUPS.get(t, {t})
    allowed = BASE_TOOLS | extra
    if character.get("role") == "head":
        allowed |= {"team_status"}  # only the Head can see the whole team
    return allowed


def filter_tools(tools: list, allowed: set[str]) -> list:
    return [t for t in tools if getattr(t, "name", "") in allowed]
