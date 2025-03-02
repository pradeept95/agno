from textwrap import dedent

from agno.agent import Agent
from agno.models.google.gemini import Gemini
from agno.models.openai import OpenAIChat
from agno.team.team import Team

player_1 = Agent(
    name="Player 1",
    role="Play Tic Tac Toe",
    model=OpenAIChat(id="gpt-4.5-preview"),
    add_name_to_instructions=True,
    instructions=dedent("""
    You are a Tic Tac Toe player.
    You will be given a Tic Tac Toe board and a player to play against.
    You will need to play the game and try to win.
    """),
)

player_2 = Agent(
    name="Player 2",
    role="Play Tic Tac Toe",
    model=Gemini(id="gemini-2.0-flash"),
    add_name_to_instructions=True,
    instructions=dedent("""
    You are a Tic Tac Toe player.
    You will be given a Tic Tac Toe board and a player to play against.
    You will need to play the game and try to win.
    """),
)


agent_team = Team(
    name="Tic Tac Toe Team",
    mode="coordinator",
    model=OpenAIChat("gpt-4o"),
    success_criteria="The game is won by one of the players.",
    members=[player_1, player_2],
    instructions=[
        "You are a games master.",
        "Initialize the board state as an empty 3x3 grid with numbers 1-9.",
        "Ask the players to make their moves one by one and wait for their responses.",
        "After each move, store the updated board state so that players have access to the board state.",
        "Don't confirm the results of the game afterwards, just report the final board state and the results.",
        "You have to stop the game when one of the players has won.",
    ],
    send_team_context_to_members=True,
    send_team_member_interactions_to_members=False,
    update_team_context=True,
    show_tool_calls=True,
    debug_mode=True,
    markdown=True,
    show_members_responses=True,
)

agent_team.print_response(
    message="Start the Tic Tac Toe game",
    stream=True,
    stream_intermediate_steps=True,
)
