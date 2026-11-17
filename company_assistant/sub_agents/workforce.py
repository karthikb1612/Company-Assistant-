from typing import Any

from google.adk.agents import Agent

from company_assistant.sub_agents.common import make_agent
from company_assistant.tools.workforce_tools import (
    get_attrition_stats,
    get_bench_summary,
    get_headcount,
    get_layoff_count,
    list_bench_employees,
    list_employees,
    list_new_hires,
)

TOOLS = [
    get_layoff_count,
    get_attrition_stats,
    get_bench_summary,
    list_bench_employees,
    list_new_hires,
    list_employees,
    get_headcount,
]


def create_agent(model: Any) -> Agent:
    return make_agent(
        "workforce_agent",
        "Answers headcount, bench, new-hire, layoff, attrition, and employee-list questions.",
        list(TOOLS),
        model,
    )
