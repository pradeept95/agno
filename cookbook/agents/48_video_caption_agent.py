from phi.agent import Agent
from phi.model.openai import OpenAIChat
from phi.tools.moviepy_video_tools import MoviePyVideoTools
from phi.tools.openai import OpenAITools
from phi.utils.download_stream_file import download_video
from pathlib import Path


# Create a single tool instance instead of multiple ones
video_tools = MoviePyVideoTools(process_video=True, generate_captions=True, embed_captions=True)

# Define the tools schema for OpenAI
openai_tools = OpenAITools()

video_caption_agent = Agent(
    name="Video Caption Generator Agent",
    model=OpenAIChat(
        id="gpt-4o",
    ),
    tools=[video_tools, openai_tools],
    description="You are an AI agent that can generate and embed captions for videos.",
    instructions=[
        "When a user provides a video, process it to generate captions.",
        "Use the video processing tools in this sequence:",
        "1. Extract audio from the video using extract_audio",
        "2. Transcribe the audio using transcribe_audio",
        "3. Generate SRT captions using create_srt",
        "4. Embed captions into the video using embed_captions",
    ],
    markdown=True,
)


# Create temp directory if it doesn't exist
temp_dir = Path("/tmp/video_captions")
temp_dir.mkdir(parents=True, exist_ok=True)


video_caption_agent.print_response(
    "Generate captions for cookbook/examples/caption_video_tool/trump.mp4 and embed them in the video"
)
# video_caption_agent.print_response(
#     "read the captions for /Users/ayushjha/Downloads/videoplayback (1).mp4 and summarize them"
# )
