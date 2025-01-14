"""Run `pip install duckdb` to install dependencies."""

from textwrap import dedent
from agno.agent import Agent
from agno.models.deepseek import DeepSeekChat
from agno.tools.duckdb import DuckDbTools

duckdb_tools = DuckDbTools(create_tables=False, export_tables=False, summarize_tables=False)
duckdb_tools.create_table_from_path(
    path="https://phidata-public.s3.amazonaws.com/demo_data/IMDB-Movie-Data.csv", table="movies"
)

agent = Agent(
    model=DeepSeekChat(id="deepseek-chat"),
    tools=[duckdb_tools],
    markdown=True,
    show_tool_calls=True,
    additional_context=dedent("""\
    You have access to the following tables:
    - movies: Contains information about movies from IMDB.
    """),
)
agent.print_response("What is the average rating of movies?", stream=False)
