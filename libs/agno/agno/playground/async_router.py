import json
from dataclasses import asdict
from io import BytesIO
from typing import AsyncGenerator, List, Optional, cast

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from agno.agent.agent import Agent, RunResponse
from agno.media import Audio, Image, Video
from agno.playground.operator import (
    format_tools,
    get_agent_by_id,
    get_session_title,
    get_session_title_from_workflow_session,
    get_workflow_by_id,
)
from agno.playground.schemas import (
    AgentGetResponse,
    AgentModel,
    AgentRenameRequest,
    AgentSessionsResponse,
    WorkflowGetResponse,
    WorkflowRenameRequest,
    WorkflowRunRequest,
    WorkflowSessionResponse,
    WorkflowsGetResponse,
)
from agno.run.response import RunEvent
from agno.storage.agent.session import AgentSession
from agno.storage.workflow.session import WorkflowSession
from agno.utils.log import logger
from agno.workflow.workflow import Workflow


def get_async_playground_router(
    agents: Optional[List[Agent]] = None, workflows: Optional[List[Workflow]] = None
) -> APIRouter:
    playground_router = APIRouter(prefix="/playground", tags=["Playground"])

    if agents is None and workflows is None:
        raise ValueError("Either agents or workflows must be provided.")

    @playground_router.get("/status")
    async def playground_status():
        return {"playground": "available"}

    @playground_router.get("/agents", response_model=List[AgentGetResponse])
    async def get_agents():
        agent_list: List[AgentGetResponse] = []
        if agents is None:
            return agent_list

        for agent in agents:
            agent_tools = agent.get_tools()
            formatted_tools = format_tools(agent_tools)

            name = agent.model.name or agent.model.__class__.__name__ if agent.model else None
            provider = agent.model.provider or agent.model.__class__.__name__ if agent.model else ""
            model_id = agent.model.id if agent.model else None

            # Create an agent_id if its not set on the agent
            if agent.agent_id is None:
                agent.set_agent_id()

            if provider and model_id:
                provider = f"{provider} {model_id}"
            elif name and model_id:
                provider = f"{name} {model_id}"
            elif model_id:
                provider = model_id
            else:
                provider = ""

            agent_list.append(
                AgentGetResponse(
                    agent_id=agent.agent_id,
                    name=agent.name,
                    model=AgentModel(
                        name=name,
                        model=model_id,
                        provider=provider,
                    ),
                    add_context=agent.add_context,
                    tools=formatted_tools,
                    memory={"name": agent.memory.db.__class__.__name__} if agent.memory and agent.memory.db else None,
                    storage={"name": agent.storage.__class__.__name__} if agent.storage else None,
                    knowledge={"name": agent.knowledge.__class__.__name__} if agent.knowledge else None,
                    description=agent.description,
                    instructions=agent.instructions,
                )
            )

        return agent_list

    async def chat_response_streamer(
        agent: Agent,
        message: str,
        images: Optional[List[Image]] = None,
        audio: Optional[List[Audio]] = None,
        videos: Optional[List[Video]] = None,
    ) -> AsyncGenerator:
        try:
            run_response = await agent.arun(
                message,
                images=images,
                audio=audio,
                videos=videos,
                stream=True,
                stream_intermediate_steps=True,
            )
            async for run_response_chunk in run_response:
                run_response_chunk = cast(RunResponse, run_response_chunk)
                yield run_response_chunk.to_json()
        except Exception as e:
            error_response = RunResponse(
                content=str(e),
                event=RunEvent.run_error,
            )
            yield error_response.to_json()
            return

    async def process_image(file: UploadFile) -> Image:
        content = file.file.read()

        return Image(content=content)

    async def process_audio(file: UploadFile) -> Audio:
        content = file.file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file")
        format = None
        if file.filename and "." in file.filename:
            format = file.filename.split(".")[-1].lower()
        elif file.content_type:
            format = file.content_type.split("/")[-1]

        return Audio(content=content, format=format)

    @playground_router.post("/agents/{agent_id}/runs")
    async def create_agent_run(
        agent_id: str,
        message: str = Form(...),
        stream: bool = Form(True),
        monitor: bool = Form(False),
        session_id: Optional[str] = Form(None),
        user_id: Optional[str] = Form(None),
        files: Optional[List[UploadFile]] = File(None),
        transcribe_audio: bool = Form(False),
    ):
        logger.debug(f"AgentRunRequest: {message} {session_id} {user_id} {agent_id}")
        agent = get_agent_by_id(agent_id, agents)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")

        if session_id is not None:
            logger.debug(f"Continuing session: {session_id}")
        else:
            logger.debug("Creating new session")

        # Create a new instance of this agent
        new_agent_instance = agent.deep_copy(update={"session_id": session_id})
        new_agent_instance.session_name = None
        if user_id is not None:
            new_agent_instance.user_id = user_id

        if monitor:
            new_agent_instance.monitoring = True
        else:
            new_agent_instance.monitoring = False

        base64_images: List[Image] = []
        base64_audios: List[Audio] = []
        audio_transcriptions: List[str] = []

        if files:
            for file in files:
                logger.info(f"Processing file: {file.content_type}")
                if file.content_type in ["image/png", "image/jpeg", "image/jpg", "image/webp"]:
                    try:
                        base64_image = await process_image(file)
                        base64_images.append(base64_image)
                    except Exception as e:
                        logger.error(f"Error processing image {file.filename}: {e}")
                        continue
                elif file.content_type in ["audio/wav", "audio/mp3", "audio/mpeg", "audio/ogg"]:
                    try:
                        # Process the audio file
                        base64_audio = await process_audio(file)
                        base64_audios.append(base64_audio)
                        
                        # If transcribe_audio is enabled and the model is Groq, transcribe the audio
                        if transcribe_audio:
                            # Check if the model is Groq
                            from agno.models.groq.groq import Groq
                            if hasattr(new_agent_instance.model, 'provider') and new_agent_instance.model.provider == "Groq" and isinstance(new_agent_instance.model, Groq):
                                logger.info(f"Transcribing audio file: {file.filename}")
                                
                                try:
                                    # Transcribe the audio directly using the model's transcription method
                                    # The transcription will be handled in the format_message method
                                    # so we don't need to do anything special here
                                    logger.info("Audio will be transcribed during message processing")
                                except Exception as e:
                                    logger.error(f"Error during audio transcription setup: {str(e)}")
                            else:
                                logger.info("Audio transcription is only supported with Groq models")
                    except Exception as e:
                        logger.error(f"Error processing audio {file.filename}: {e}")
                        continue
                else:
                    # Check for knowledge base before processing documents
                    if new_agent_instance.knowledge is None:
                        raise HTTPException(status_code=404, detail="KnowledgeBase not found")

                    if file.content_type == "application/pdf":
                        from agno.document.reader.pdf_reader import PDFReader

                        contents = await file.read()
                        pdf_file = BytesIO(contents)
                        pdf_file.name = file.filename
                        file_content = PDFReader().read(pdf_file)
                        if new_agent_instance.knowledge is not None:
                            new_agent_instance.knowledge.load_documents(file_content)
                    elif file.content_type == "text/csv":
                        from agno.document.reader.csv_reader import CSVReader

                        contents = await file.read()
                        csv_file = BytesIO(contents)
                        csv_file.name = file.filename
                        file_content = CSVReader().read(csv_file)
                        if new_agent_instance.knowledge is not None:
                            new_agent_instance.knowledge.load_documents(file_content)
                    elif file.content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                        from agno.document.reader.docx_reader import DocxReader

                        contents = await file.read()
                        docx_file = BytesIO(contents)
                        docx_file.name = file.filename
                        file_content = DocxReader().read(docx_file)
                        if new_agent_instance.knowledge is not None:
                            new_agent_instance.knowledge.load_documents(file_content)
                    elif file.content_type == "text/plain":
                        from agno.document.reader.text_reader import TextReader

                        contents = await file.read()
                        text_file = BytesIO(contents)
                        text_file.name = file.filename
                        file_content = TextReader().read(text_file)
                        if new_agent_instance.knowledge is not None:
                            new_agent_instance.knowledge.load_documents(file_content)

                    elif file.content_type == "application/json":
                        from agno.document.reader.json_reader import JSONReader

                        contents = await file.read()
                        json_file = BytesIO(contents)
                        json_file.name = file.filename
                        file_content = JSONReader().read(json_file)
                        if new_agent_instance.knowledge is not None:
                            new_agent_instance.knowledge.load_documents(file_content)
                    else:
                        raise HTTPException(status_code=400, detail="Unsupported file type")

        # Append transcriptions to the message if any
        if audio_transcriptions:
            transcription_text = "\n\n".join([f"Audio Transcription: {t}" for t in audio_transcriptions])
            if message:
                message = f"{message}\n\n{transcription_text}"
            else:
                message = transcription_text

        if stream:
            return StreamingResponse(
                chat_response_streamer(
                    new_agent_instance,
                    message,
                    images=base64_images if base64_images else None,
                    audio=base64_audios if base64_audios else None,
                ),
                media_type="text/event-stream",
            )
        else:
            run_response = cast(
                RunResponse,
                await new_agent_instance.arun(
                    message=message,
                    images=base64_images if base64_images else None,
                    audio=base64_audios if base64_audios else None,
                    stream=False,
                ),
            )
            return run_response

    @playground_router.get("/agents/{agent_id}/sessions")
    async def get_all_agent_sessions(agent_id: str, user_id: str = Query(..., min_length=1)):
        logger.debug(f"AgentSessionsRequest: {agent_id} {user_id}")
        agent = get_agent_by_id(agent_id, agents)
        if agent is None:
            return JSONResponse(status_code=404, content="Agent not found.")

        if agent.storage is None:
            return JSONResponse(status_code=404, content="Agent does not have storage enabled.")

        agent_sessions: List[AgentSessionsResponse] = []
        all_agent_sessions: List[AgentSession] = agent.storage.get_all_sessions(user_id=user_id)
        for session in all_agent_sessions:
            title = get_session_title(session)
            agent_sessions.append(
                AgentSessionsResponse(
                    title=title,
                    session_id=session.session_id,
                    session_name=session.session_data.get("session_name") if session.session_data else None,
                    created_at=session.created_at,
                )
            )
        return agent_sessions

    @playground_router.get("/agents/{agent_id}/sessions/{session_id}")
    async def get_agent_session(agent_id: str, session_id: str, user_id: str = Query(..., min_length=1)):
        logger.debug(f"AgentSessionsRequest: {agent_id} {user_id} {session_id}")
        agent = get_agent_by_id(agent_id, agents)
        if agent is None:
            return JSONResponse(status_code=404, content="Agent not found.")

        if agent.storage is None:
            return JSONResponse(status_code=404, content="Agent does not have storage enabled.")

        agent_session: Optional[AgentSession] = agent.storage.read(session_id, user_id)
        if agent_session is None:
            return JSONResponse(status_code=404, content="Session not found.")

        return agent_session

    @playground_router.post("/agents/{agent_id}/sessions/{session_id}/rename")
    async def rename_agent_session(agent_id: str, session_id: str, body: AgentRenameRequest):
        agent = get_agent_by_id(agent_id, agents)
        if agent is None:
            return JSONResponse(status_code=404, content=f"couldn't find agent with {agent_id}")

        if agent.storage is None:
            return JSONResponse(status_code=404, content="Agent does not have storage enabled.")

        all_agent_sessions: List[AgentSession] = agent.storage.get_all_sessions(user_id=body.user_id)
        for session in all_agent_sessions:
            if session.session_id == session_id:
                agent.session_id = session_id
                agent.rename_session(body.name)
                return JSONResponse(content={"message": f"successfully renamed session {session.session_id}"})

        return JSONResponse(status_code=404, content="Session not found.")

    @playground_router.delete("/agents/{agent_id}/sessions/{session_id}")
    async def delete_agent_session(agent_id: str, session_id: str, user_id: str = Query(..., min_length=1)):
        agent = get_agent_by_id(agent_id, agents)
        if agent is None:
            return JSONResponse(status_code=404, content="Agent not found.")

        if agent.storage is None:
            return JSONResponse(status_code=404, content="Agent does not have storage enabled.")

        all_agent_sessions: List[AgentSession] = agent.storage.get_all_sessions(user_id=user_id)
        for session in all_agent_sessions:
            if session.session_id == session_id:
                agent.delete_session(session_id)
                return JSONResponse(content={"message": f"successfully deleted session {session_id}"})

        return JSONResponse(status_code=404, content="Session not found.")

    @playground_router.get("/workflows", response_model=List[WorkflowsGetResponse])
    async def get_workflows():
        if workflows is None:
            return []

        return [
            WorkflowsGetResponse(
                workflow_id=str(workflow.workflow_id),
                name=workflow.name,
                description=workflow.description,
            )
            for workflow in workflows
        ]

    @playground_router.get("/workflows/{workflow_id}", response_model=WorkflowGetResponse)
    async def get_workflow(workflow_id: str):
        workflow = get_workflow_by_id(workflow_id, workflows)
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")

        return WorkflowGetResponse(
            workflow_id=workflow.workflow_id,
            name=workflow.name,
            description=workflow.description,
            parameters=workflow._run_parameters or {},
            storage=workflow.storage.__class__.__name__ if workflow.storage else None,
        )

    @playground_router.post("/workflows/{workflow_id}/runs")
    async def create_workflow_run(workflow_id: str, body: WorkflowRunRequest):
        # Retrieve the workflow by ID
        workflow = get_workflow_by_id(workflow_id, workflows)
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")

        if body.session_id is not None:
            logger.debug(f"Continuing session: {body.session_id}")
        else:
            logger.debug("Creating new session")

        # Create a new instance of this workflow
        new_workflow_instance = workflow.deep_copy(update={"workflow_id": workflow_id, "session_id": body.session_id})
        new_workflow_instance.user_id = body.user_id
        new_workflow_instance.session_name = None

        # Return based on the response type
        try:
            if new_workflow_instance._run_return_type == "RunResponse":
                # Return as a normal response
                return new_workflow_instance.run(**body.input)
            else:
                # Return as a streaming response
                return StreamingResponse(
                    (json.dumps(asdict(result)) for result in new_workflow_instance.run(**body.input)),
                    media_type="text/event-stream",
                )
        except Exception as e:
            # Handle unexpected runtime errors
            raise HTTPException(status_code=500, detail=f"Error running workflow: {str(e)}")

    @playground_router.get("/workflows/{workflow_id}/sessions", response_model=List[WorkflowSessionResponse])
    async def get_all_workflow_sessions(workflow_id: str, user_id: str = Query(..., min_length=1)):
        # Retrieve the workflow by ID
        workflow = get_workflow_by_id(workflow_id, workflows)
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

        # Ensure storage is enabled for the workflow
        if not workflow.storage:
            raise HTTPException(status_code=404, detail="Workflow does not have storage enabled")

        # Retrieve all sessions for the given workflow and user
        try:
            all_workflow_sessions: List[WorkflowSession] = workflow.storage.get_all_sessions(
                user_id=user_id, workflow_id=workflow_id
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error retrieving sessions: {str(e)}")

        # Return the sessions
        return [
            WorkflowSessionResponse(
                title=get_session_title_from_workflow_session(session),
                session_id=session.session_id,
                session_name=session.session_data.get("session_name") if session.session_data else None,
                created_at=session.created_at,
            )
            for session in all_workflow_sessions
        ]

    @playground_router.get("/workflows/{workflow_id}/sessions/{session_id}")
    async def get_workflow_session(workflow_id: str, session_id: str, user_id: str = Query(..., min_length=1)):
        # Retrieve the workflow by ID
        workflow = get_workflow_by_id(workflow_id, workflows)
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

        # Ensure storage is enabled for the workflow
        if not workflow.storage:
            raise HTTPException(status_code=404, detail="Workflow does not have storage enabled")

        # Retrieve the specific session
        try:
            workflow_session: Optional[WorkflowSession] = workflow.storage.read(session_id, user_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error retrieving session: {str(e)}")

        if not workflow_session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Return the session
        return workflow_session

    @playground_router.post("/workflows/{workflow_id}/sessions/{session_id}/rename")
    async def rename_workflow_session(workflow_id: str, session_id: str, body: WorkflowRenameRequest):
        workflow = get_workflow_by_id(workflow_id, workflows)
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        workflow.session_id = session_id
        workflow.rename_session(body.name)
        return JSONResponse(content={"message": f"successfully renamed workflow {workflow.name}"})

    @playground_router.delete("/workflows/{workflow_id}/sessions/{session_id}")
    async def delete_workflow_session(workflow_id: str, session_id: str):
        workflow = get_workflow_by_id(workflow_id, workflows)
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")

        workflow.delete_session(session_id)
        return JSONResponse(content={"message": f"successfully deleted workflow {workflow.name}"})

    @playground_router.post("/transcribe")
    async def transcribe_audio_file(
        file: UploadFile = File(...),
        agent_id: Optional[str] = Form(None),
        language: Optional[str] = Form(None),
        prompt: Optional[str] = Form(None),
        response_format: Optional[str] = Form("text"),
        temperature: Optional[float] = Form(None),
    ):
        """
        Transcribe an audio file using the Groq API.
        
        Args:
            file: The audio file to transcribe
            agent_id: Optional agent ID to use for transcription (must be a Groq model)
            language: Optional language code (ISO-639-1)
            prompt: Optional text to guide the model
            response_format: Format of the response (text, json, srt, verbose_json)
            temperature: Sampling temperature between 0 and 1
            
        Returns:
            The transcription result
        """
        if not file:
            raise HTTPException(status_code=400, detail="No file provided")
            
        if file.content_type not in ["audio/wav", "audio/mp3", "audio/mpeg", "audio/ogg"]:
            raise HTTPException(status_code=400, detail="Unsupported file type. Must be wav, mp3, or ogg")
            
        # Get the file format
        file_format = None
        if file.filename and "." in file.filename:
            file_format = file.filename.split(".")[-1].lower()
        elif file.content_type:
            file_format = file.content_type.split("/")[-1]
            
        logger.info(f"Processing audio file: {file.filename} (format: {file_format})")
            
        # Process the audio file into an Audio object
        try:
            audio_obj = await process_audio(file)
            if not audio_obj or not audio_obj.content:
                raise ValueError("Failed to process audio file - no content")
            
            logger.info(f"Audio processed successfully. Size: {len(audio_obj.content)} bytes")
        except Exception as e:
            logger.error(f"Error processing audio file: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            raise HTTPException(status_code=400, detail=f"Error processing audio file: {str(e)}")
            
        # Get the Groq model
        groq_model = None
        if agent_id:
            try:
                agent = get_agent_by_id(agent_id, agents)
                if agent is None:
                    raise HTTPException(status_code=404, detail="Agent not found")
                    
                if not hasattr(agent.model, 'provider') or agent.model.provider != "Groq":
                    raise HTTPException(status_code=400, detail="Agent must use a Groq model")
                    
                from agno.models.groq.groq import Groq
                if isinstance(agent.model, Groq):
                    groq_model = agent.model
                    logger.info(f"Using Groq model from agent: {agent_id}")
                else:
                    raise HTTPException(status_code=400, detail="Agent must use a Groq model")
            except Exception as e:
                logger.error(f"Error getting agent model: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error getting agent model: {str(e)}")
        else:
            # Create a new Groq model instance
            try:
                from agno.models.groq.groq import Groq
                groq_model = Groq()
                logger.info("Created new Groq model instance")
            except Exception as e:
                logger.error(f"Error creating Groq model: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error creating Groq model: {str(e)}")
            
        try:
            # Transcribe the audio using the Audio object
            logger.info("Starting audio transcription...")
            transcription = await groq_model.atranscribe_audio(
                audio_data=audio_obj,
                file_format=file_format,
                language=language,
                prompt=prompt,
                response_format=response_format,
                temperature=temperature
            )
            
            # Return the transcription
            result = {"transcription": transcription.text if hasattr(transcription, "text") else transcription}
            logger.info(f"Transcription successful: {result['transcription'][:100]}...")
            return result
        except httpx.ConnectError as e:
            logger.error(f"Connection error when calling Groq API: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            raise HTTPException(
                status_code=503, 
                detail=f"Connection error when calling Groq API: {str(e)}. Please check your network connection and API endpoint."
            )
        except Exception as e:
            logger.error(f"Error transcribing audio: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            raise HTTPException(status_code=500, detail=f"Error transcribing audio: {str(e)}")

    return playground_router
