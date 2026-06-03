# DevTwin

DevTwin is an AI-powered developer digital twin platform. It combines repository intelligence, research intelligence, a Neo4j knowledge graph, LangGraph multi-agent orchestration, and visual dashboards into one local full-stack system.

## Current Capabilities

### Repository Intelligence

- Analyze GitHub repositories through the FastAPI backend.
- Detect tech stacks, API routes, dependency graphs, and architecture hints.
- Generate structured AI repository summaries from parsed metadata.
- Render repository summaries and dependency graphs in the Next.js frontend.

### Research Intelligence

- Upload PDFs and parse them into Markdown.
- Chunk research papers deterministically.
- Generate local embeddings with FastEmbed.
- Store vectors in local Qdrant file mode.
- Retrieve relevant research chunks with citations.
- Generate research manifests and citation-grounded paper chat responses.

### Knowledge Graph Layer

- Store repository, paper, technology, framework, and concept nodes in Neo4j.
- Connect a central `User` node to repositories and papers.
- Infer personal skill strengths from owned repositories and read papers.
- Query the graph as a React Flow-compatible subgraph.

### Multi-Agent Orchestration

- LangGraph state machine with shared agent state.
- Planner agent routes to:
  - Knowledge Agent for Neo4j personal graph context.
  - Code Agent for repository architecture and tech stack context.
  - Research Agent for Qdrant semantic research retrieval.
  - Synthesizer Agent for final personalized answers.
- `/chat` frontend page invokes the multi-agent graph and displays agent context traces.

### Visual Intelligence

- Personal twin dashboard at `/twin`.
- Skill radar chart.
- Skill gap panel.
- Temporal skill evolution chart powered by Neo4j and Recharts.

## Tech Stack

Backend:

- FastAPI
- PostgreSQL
- SQLAlchemy 2.x async
- Alembic
- Pydantic v2
- Neo4j async driver
- LangGraph
- LangChain Core
- Gemini via `langchain-google-genai`
- FastEmbed
- Qdrant local file mode
- PyMuPDF / pymupdf4llm

Frontend:

- Next.js
- TypeScript
- Tailwind CSS
- React Flow
- Recharts
- Lucide React

## Project Structure

```text
DevTwin/
  backend/
    app/
      api/
      api/v1/
      core/
      models/
      schemas/
      services/
        agents/
        graph/
        research/
        twin/
    scripts/
    alembic/
  frontend/
    src/
      app/
      components/
      lib/
```

## Backend Setup

From the backend directory:

```powershell
cd C:\Users\pujar\Documents\DevTwin\backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create or update `backend\.env` using `backend\.env.example`.

Important environment variables:

```env
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/devtwin
GEMINI_API_KEY=your_key_here
NEO4J_URI=neo4j+ssc://your-instance.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password_here
GITHUB_TOKEN=optional_github_token
```

Run the backend:

```powershell
cd C:\Users\pujar\Documents\DevTwin\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

API docs:

```text
http://localhost:8000/docs
```

## Frontend Setup

From the frontend directory:

```powershell
cd C:\Users\pujar\Documents\DevTwin\frontend
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:3000
```

Main pages:

- `/` - Repository Intelligence
- `/research` - Research Brain
- `/graph` - Visual Knowledge Graph
- `/twin` - Personal Learning Graph
- `/chat` - Multi-Agent Chat

## Useful Commands

Backend compile check:

```powershell
cd C:\Users\pujar\Documents\DevTwin\backend
.\.venv\Scripts\python.exe -m compileall app scripts
```

Frontend typecheck:

```powershell
cd C:\Users\pujar\Documents\DevTwin\frontend
npm run typecheck
```

Frontend production build:

```powershell
cd C:\Users\pujar\Documents\DevTwin\frontend
npm run build
```

## Graph Ingestion Scripts

Seed the central user node:

```powershell
cd C:\Users\pujar\Documents\DevTwin\backend
.\.venv\Scripts\python.exe scripts\seed_user_graph.py
```

Ingest repository intelligence into Neo4j:

```powershell
.\.venv\Scripts\python.exe scripts\ingest_repo_graph.py
```

Ingest research manifests into Neo4j:

```powershell
.\.venv\Scripts\python.exe scripts\ingest_paper_graph.py
```

## Research Pipeline

The `/research` page runs this backend sequence:

1. `POST /api/v1/research/upload`
2. `POST /api/v1/research/{paper_id}/chunk`
3. `POST /api/v1/research/{paper_id}/embed`
4. `POST /api/v1/research/{paper_id}/manifest`

After that, papers can be queried and used by the LangGraph research agent.

## Multi-Agent Chat Endpoint

```http
POST /api/v1/chat/invoke
```

Request:

```json
{
  "query": "What should I build next to improve my ML engineer profile?"
}
```

Response:

```json
{
  "answer": "Final synthesized response",
  "used_agents": ["Knowledge Agent", "Code Agent", "Research Agent"],
  "context_snippets": {
    "graph": ["..."],
    "code": ["..."],
    "research": ["..."]
  }
}
```

## Temporal Skill Evolution

The `/api/v1/twin/evolution` endpoint traverses:

```text
(User)-[:OWNER_OF]->(Repository)-[:USES]->(Technology/Framework)
(User)-[:READ]->(Paper)-[:MENTIONS_TECH]->(Technology/Framework)
```

It returns cumulative yearly skill scores for Recharts:

```json
[
  {
    "date": "2026",
    "Python": 100,
    "FastAPI": 60,
    "Next.js": 30
  }
]
```

The `/twin` page visualizes this as a stacked area chart.

## Notes

- Qdrant runs locally from `backend\.qdrant`; no Docker Qdrant container is required.
- FastEmbed model files are cached under `backend\.fastembed_cache`.
- Neo4j AuraDB may require `neo4j+ssc://` locally on Windows if certificate verification fails.
- Gemini quota or model access errors are handled with safe fallbacks in the agent layer.
