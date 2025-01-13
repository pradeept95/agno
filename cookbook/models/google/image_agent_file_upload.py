from pathlib import Path

from google.generativeai.types import file_types

from agno.agent import Agent
from agno.media import ImageInput
from agno.models.google import Gemini
from agno.tools.duckduckgo import DuckDuckGo
from google.generativeai import upload_file

agent = Agent(
    model=Gemini(id="gemini-2.0-flash-exp"),
    tools=[DuckDuckGo()],
    markdown=True,
)
# Please download the image using
# wget https://upload.wikimedia.org/wikipedia/commons/b/bf/Krakow_-_Kosciol_Mariacki.jpg
image_path = Path(__file__).parent.joinpath("Krakow_-_Kosciol_Mariacki.jpg")
image_file: file_types.File = upload_file(image_path)
print(f"Uploaded image: {image_file}")

agent.print_response(
    "Tell me about this image and give me the latest news about it.",
    images=[ImageInput(content=image_file)],
    stream=True,
)
