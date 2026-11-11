import logging

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_groq import ChatGroq
from langchain_nvidia_ai_endpoints import ChatNVIDIA

from app.ai.manifest_routing import (
    is_architecture_question,
    is_call_question,
    load_callgraph_context,
    load_manifest_context,
)
from app.core.config import settings
from app.models.chat import ChatMessage
from app.services.embedding_service import (
    get_chroma_client,
    get_collection_name,
    get_embedding_function,
)

logger = logging.getLogger(__name__)

RETRIEVAL_TOP_K = 5

MANIFEST_CONTEXT_HEADER = (
    "Repository manifest (deterministic structure summary). Use this to answer "
    "questions about repository layout, languages, entrypoints and module "
    "boundaries. It covers files that are deliberately not in the vector index, "
    "such as documentation and configuration."
)

CALLGRAPH_CONTEXT_HEADER = (
    "Call graph (deterministic, from stored call sites). Each entry records "
    "who calls whom and at which file:line. The bracketed label records how "
    "the target was found — same_file, import, unique — or that it is "
    "unresolved (standard library, third-party or dynamic dispatch); treat "
    "unresolved entries as call sites whose target could not be verified."
)

_llm = None


def _build_llm():
    provider = settings.LLM_PROVIDER.lower()

    if provider == "groq":
        if not settings.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY is not set in the environment.")
        return ChatGroq(
            model=settings.LLM_MODEL_GROQ,
            temperature=settings.LLM_TEMPERATURE,
            api_key=settings.GROQ_API_KEY,
        )

    if provider == "nvidia":
        if not settings.NVIDIA_API_KEY:
            raise ValueError("NVIDIA_API_KEY is not set in the environment.")
        return ChatNVIDIA(
            model=settings.LLM_MODEL_NVIDIA,
            temperature=settings.LLM_TEMPERATURE,
            api_key=settings.NVIDIA_API_KEY,
        )

    raise ValueError(
        f"Unsupported LLM_PROVIDER '{provider}'. Use 'nvidia' or 'groq'."
    )


def get_llm():
    """Return the configured LLM, building it on first use."""
    global _llm
    if _llm is None:
        logger.info("Initializing %s LLM", settings.LLM_PROVIDER)
        _llm = _build_llm()
    return _llm


class ManifestOnlyRetriever:
    """
    Retriever stand-in for a repository with no embedded chunks.

    A docs/config-only repository has no Chroma collection, so LangChain cannot
    build a retriever over it. Returning no documents keeps the rest of the
    pipeline working: architecture questions are answered from the manifest, and
    an implementation question correctly finds no code rather than 500ing.
    """

    def invoke(self, question: str) -> list:
        logger.info("No vector index for this repository; manifest-only retrieval")
        return []

    def get_relevant_documents(self, question: str) -> list:
        return self.invoke(question)


def get_retriever(repo_id: str):
    """
    Build a LangChain retriever over the ChromaDB collection for a repository.
    """
    from langchain_chroma import Chroma

    try:
        vectorstore = Chroma(
            client=get_chroma_client(),
            collection_name=get_collection_name(repo_id),
            embedding_function=get_embedding_function(),
        )
        return vectorstore.as_retriever(search_kwargs={"k": RETRIEVAL_TOP_K})
    except Exception as exc:
        # A missing collection is only fatal when the repository really was
        # meant to have one. If there is no collection at all, fall back to
        # manifest-only retrieval instead of refusing the question.
        try:
            get_chroma_client().get_collection(name=get_collection_name(repo_id))
        except Exception:
            logger.warning(
                "No Chroma collection for %s; using manifest-only retrieval", repo_id
            )
            return ManifestOnlyRetriever()

        raise ValueError(
            "Repository embeddings not found. Please ensure the repository has "
            f"been fully processed. ({exc})"
        ) from exc


def _collect_context(db, repo_id, question: str, retriever) -> str:
  """
  Assemble prompt context from vector retrieval, plus the manifest and/or call
  graph when the question is about repository shape or call relations.

  Manifest and call-graph context are prepended rather than substituted:
  routing only adds information, so a mixed question still gets its code.
  """
  relevant_docs = retriever.invoke(question)
  context_parts = [doc.page_content for doc in relevant_docs]

  # repo_id is optional in the chat API; a session without one has no rows to
  # route to, so only routed questions pay for the lookups.
  if repo_id and is_call_question(question):
      call_context = load_callgraph_context(db, repo_id, question)
      if call_context:
          logger.info(
              "Call question: injecting call graph context (%d chars)",
              len(call_context),
          )
          context_parts.insert(0, f"{CALLGRAPH_CONTEXT_HEADER}\n\n{call_context}")

  if is_architecture_question(question):
      manifest_context = load_manifest_context(db, repo_id)
      if manifest_context:
          logger.info(
              "Architecture question: injecting manifest context (%d chars)",
              len(manifest_context),
          )
          context_parts.insert(0, f"{MANIFEST_CONTEXT_HEADER}\n\n{manifest_context}")

  return "\n\n".join(context_parts)


def _build_chain(db, session_id: int, question: str, retriever, llm, repo_id=None):
  """Shared history loading + context retrieval + prompt construction."""
  # 1. Retrieve history from PostgreSQL
  raw_history = (
      db.query(ChatMessage)
      .filter(ChatMessage.session_id == session_id)
      .order_by(ChatMessage.created_at.asc())
      .all()
  )

  chat_history = []
  for msg in raw_history:
    if msg.role == "human":
      chat_history.append(HumanMessage(content=msg.content))
    else:
      chat_history.append(AIMessage(content=msg.content))

  # 2. Retrieve vector store context (and manifest context when relevant)
  context = _collect_context(db, repo_id, question, retriever)

  # 3. Build prompt with history buffer
  prompt = ChatPromptTemplate.from_messages([
      (
          "system",
          (
              "You are an expert software engineer analyzing a codebase. Use"
              " the codebase context and previous conversation history to answer"
              " accurately.\n\nContext:\n{context}"
          ),
      ),
      MessagesPlaceholder(variable_name="chat_history"),
      ("human", "{question}"),
  ])

  # 4. Wire up the chain
  chain = prompt | llm
  inputs = {
      "context": context,
      "chat_history": chat_history,
      "question": question,
  }
  return chain, inputs


def ask_codebase_with_history(
    db, session_id: int, question: str, retriever, llm, repo_id=None
) -> str:
  chain, inputs = _build_chain(db, session_id, question, retriever, llm, repo_id)

  response = chain.invoke(inputs)
  return response.content


def ask_codebase_with_history_stream(
    db, session_id: int, question: str, retriever, llm, repo_id=None
):
  """
  Generator that yields the running answer text token by token and persists
  the complete AI response to the conversation when generation finishes.
  """
  chain, inputs = _build_chain(db, session_id, question, retriever, llm, repo_id)

  full_response = ""
  for chunk in chain.stream(inputs):
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
      full_response += content
    elif isinstance(content, list):
      for item in content:
        if isinstance(item, dict):
          full_response += item.get("text", "")
        elif isinstance(item, str):
          full_response += item
    else:
      full_response += str(content)
    yield full_response

  # Persist the complete AI response to the conversation
  db.add(ChatMessage(session_id=session_id, role="ai", content=full_response))
  db.commit()
