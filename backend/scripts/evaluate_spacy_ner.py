"""
Evaluate spaCy NER quality for Athena Research Brain knowledge extraction.

This script is intentionally standalone and does not create Neo4j objects.
It helps decide whether spaCy entities are graph-worthy for Phase 3.

Usage examples:
    .\\.venv\\Scripts\\python.exe scripts\\evaluate_spacy_ner.py
    .\\.venv\\Scripts\\python.exe scripts\\evaluate_spacy_ner.py --input-file sample_manifest.json
    .\\.venv\\Scripts\\python.exe scripts\\evaluate_spacy_ner.py --paper-id <uuid>
    .\\.venv\\Scripts\\python.exe scripts\\evaluate_spacy_ner.py --latest-db
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import spacy
from spacy.language import Language
from spacy.matcher import PhraseMatcher

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

logger = logging.getLogger("athena_spacy_ner_eval")


SAMPLE_TEXT = """
DevTwin Athena is an AI-powered developer digital twin platform. The Research
Brain ingests academic PDFs, parses them with pymupdf4llm and PyMuPDF, chunks
Markdown using a deterministic sliding-window algorithm, stores embeddings in
Qdrant using FastEmbed and BAAI/bge-small-en-v1.5, and supports citation-based
RAG chat with Gemini 2.5 Flash.

The system uses FastAPI, PostgreSQL, SQLAlchemy, Alembic, Pydantic, Next.js,
React Flow, and Tailwind CSS. Research concepts include vector search,
knowledge graphs, named entity recognition, transformer architectures,
self-attention, retrieval augmented generation, and digital twin memory.

Potential methodologies include deterministic parsing, semantic retrieval,
manifest generation, ontology design, rule-based extraction, hybrid NLP,
entity normalization, and citation-grounded question answering.
"""


TECHNICAL_DICTIONARY: dict[str, list[str]] = {
    "ProgrammingLanguage": [
        "Python",
        "TypeScript",
        "JavaScript",
        "SQL",
    ],
    "Framework": [
        "FastAPI",
        "Next.js",
        "React",
        "React Flow",
        "Tailwind CSS",
        "SQLAlchemy",
        "Alembic",
        "Pydantic",
    ],
    "Database": [
        "PostgreSQL",
        "Qdrant",
        "Neo4j",
    ],
    "AITool": [
        "Gemini",
        "Gemini 2.5 Flash",
        "FastEmbed",
        "BAAI/bge-small-en-v1.5",
        "spaCy",
        "pymupdf4llm",
        "PyMuPDF",
    ],
    "ResearchConcept": [
        "Knowledge Graph",
        "Knowledge Graphs",
        "Named Entity Recognition",
        "NER",
        "Transformer",
        "Transformers",
        "Self Attention",
        "Self-Attention",
        "Vector Search",
        "Retrieval Augmented Generation",
        "RAG",
        "Digital Twin",
        "Digital Twin Memory",
        "Ontology",
        "Citation",
        "Citations",
    ],
    "Methodology": [
        "Deterministic Parsing",
        "Sliding-Window",
        "Semantic Retrieval",
        "Manifest Generation",
        "Rule-Based Extraction",
        "Hybrid NLP",
        "Entity Normalization",
        "Citation-Grounded Question Answering",
    ],
}


SPACY_LABEL_TO_GRAPH_TYPE: dict[str, str] = {
    "ORG": "Organization",
    "PRODUCT": "Technology",
    "PERSON": "Person",
    "WORK_OF_ART": "PublicationOrArtifact",
    "EVENT": "Event",
    "LAW": "StandardOrRule",
    "GPE": "Location",
    "LOC": "Location",
    "NORP": "Group",
    "DATE": "TimeExpression",
}


DISCARDED_SPACY_LABELS = {
    "CARDINAL",
    "ORDINAL",
    "QUANTITY",
    "PERCENT",
    "MONEY",
    "TIME",
}


@dataclass(frozen=True)
class EntityRecord:
    """A normalized entity mention extracted by one extraction layer."""

    text: str
    label: str
    source: str
    count: int


@dataclass(frozen=True)
class LoadedText:
    """Input text plus a short source label for reporting."""

    text: str
    source: str


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    if not verbose:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate spaCy NER output for Athena knowledge graph planning.",
    )
    parser.add_argument(
        "--model",
        default="en_core_web_sm",
        help="spaCy pipeline name. Default: en_core_web_sm.",
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        help="Optional JSON, Markdown, or text file containing a manifest or paper summary.",
    )
    parser.add_argument(
        "--paper-id",
        type=uuid.UUID,
        help="Optional Paper UUID to load from the local PostgreSQL database.",
    )
    parser.add_argument(
        "--latest-db",
        action="store_true",
        help="Load the latest paper from the local PostgreSQL database.",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=12000,
        help="Maximum characters to analyze from the loaded text. Default: 12000.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optional path to write machine-readable extraction results as JSON.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args()


def load_text_from_file(path: Path) -> LoadedText:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(raw)
        return LoadedText(text=flatten_json_text(payload), source=f"file:{path}")

    return LoadedText(text=raw, source=f"file:{path}")


def flatten_json_text(value: Any) -> str:
    """Convert nested JSON into readable text for NER evaluation."""
    parts: list[str] = []

    def walk(item: Any, prefix: str = "") -> None:
        if item is None:
            return
        if isinstance(item, str):
            if item.strip():
                parts.append(f"{prefix}{item.strip()}" if prefix else item.strip())
            return
        if isinstance(item, (int, float, bool)):
            parts.append(f"{prefix}{item}" if prefix else str(item))
            return
        if isinstance(item, list):
            for child in item:
                walk(child, prefix=prefix)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                walk(child, prefix=f"{key}: ")
            return

    walk(value)
    return "\n".join(parts)


async def load_text_from_database(
    paper_id: uuid.UUID | None,
    latest: bool,
) -> LoadedText:
    """Load a paper manifest or markdown from the existing DevTwin database."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal, engine
    from app.models.research import Paper

    engine.echo = False

    async with AsyncSessionLocal() as db:
        if paper_id is not None:
            paper = await db.get(Paper, paper_id)
        elif latest:
            result = await db.execute(select(Paper).order_by(Paper.uploaded_at.desc()).limit(1))
            paper = result.scalar_one_or_none()
        else:
            paper = None

        if paper is None:
            raise ValueError("No matching paper found in the database.")

        if paper.research_manifest:
            text = flatten_json_text(paper.research_manifest)
            source = f"db:paper:{paper.id}:research_manifest"
        elif paper.raw_markdown:
            text = paper.raw_markdown
            source = f"db:paper:{paper.id}:raw_markdown"
        else:
            raise ValueError(f"Paper {paper.id} has no manifest or raw markdown.")

        return LoadedText(text=text, source=source)


def load_text(args: argparse.Namespace) -> LoadedText:
    if args.input_file:
        return load_text_from_file(args.input_file)

    if args.paper_id or args.latest_db:
        if sys.platform == "win32":
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]
        return asyncio.run(load_text_from_database(args.paper_id, args.latest_db))

    return LoadedText(text=SAMPLE_TEXT, source="built-in-athena-sample")


def load_spacy_model(model_name: str) -> Language:
    logger.info("Loading spaCy model: %s", model_name)
    try:
        return spacy.load(model_name)
    except OSError as exc:
        raise RuntimeError(
            f"spaCy model '{model_name}' is not installed. Run: "
            f".\\.venv\\Scripts\\python.exe -m spacy download {model_name}"
        ) from exc


def extract_spacy_entities(nlp: Language, text: str) -> list[EntityRecord]:
    doc = nlp(text)
    counter: Counter[tuple[str, str]] = Counter(
        (entity.text.strip(), entity.label_)
        for entity in doc.ents
        if entity.text.strip()
    )

    return [
        EntityRecord(text=text_value, label=label, source="spacy", count=count)
        for (text_value, label), count in counter.most_common()
    ]


def build_phrase_matcher(nlp: Language) -> PhraseMatcher:
    matcher = PhraseMatcher(nlp.vocab, attr="LOWER")
    for label, terms in TECHNICAL_DICTIONARY.items():
        patterns = [nlp.make_doc(term) for term in terms]
        matcher.add(label, patterns)
    return matcher


def extract_dictionary_entities(nlp: Language, text: str) -> list[EntityRecord]:
    matcher = build_phrase_matcher(nlp)
    doc = nlp.make_doc(text)
    counter: Counter[tuple[str, str]] = Counter()

    for match_id, start, end in matcher(doc):
        label = nlp.vocab.strings[match_id]
        entity_text = doc[start:end].text.strip()
        if entity_text:
            counter[(entity_text, label)] += 1

    return [
        EntityRecord(text=text_value, label=label, source="dictionary", count=count)
        for (text_value, label), count in counter.most_common()
    ]


def group_by_label(records: list[EntityRecord]) -> dict[str, list[EntityRecord]]:
    grouped: dict[str, list[EntityRecord]] = defaultdict(list)
    for record in records:
        grouped[record.label].append(record)
    return dict(sorted(grouped.items()))


def make_graph_candidates(
    spacy_records: list[EntityRecord],
    dictionary_records: list[EntityRecord],
) -> list[dict[str, Any]]:
    candidates: dict[tuple[str, str], dict[str, Any]] = {}

    for record in spacy_records:
        if record.label in DISCARDED_SPACY_LABELS:
            continue

        node_type = SPACY_LABEL_TO_GRAPH_TYPE.get(record.label)
        if not node_type:
            continue

        key = (node_type, record.text.lower())
        candidates[key] = {
            "node_type": node_type,
            "name": record.text,
            "frequency": record.count,
            "evidence_sources": [record.source],
            "raw_labels": [record.label],
        }

    for record in dictionary_records:
        key = (record.label, record.text.lower())
        if key not in candidates:
            candidates[key] = {
                "node_type": record.label,
                "name": record.text,
                "frequency": record.count,
                "evidence_sources": [record.source],
                "raw_labels": [record.label],
            }
        else:
            candidates[key]["frequency"] += record.count
            candidates[key]["evidence_sources"].append(record.source)
            candidates[key]["raw_labels"].append(record.label)

    return sorted(
        candidates.values(),
        key=lambda item: (str(item["node_type"]), -int(item["frequency"]), str(item["name"]).lower()),
    )


def print_entity_groups(title: str, records: list[EntityRecord]) -> None:
    print(f"\n{title}")
    print("=" * len(title))
    grouped = group_by_label(records)

    if not grouped:
        print("No entities extracted.")
        return

    for label, label_records in grouped.items():
        print(f"\nENTITY TYPE: {label}")
        for record in label_records:
            print(f"* {record.text} (count={record.count})")


def print_graph_candidates(candidates: list[dict[str, Any]]) -> None:
    print("\nGRAPH-DESIGN-FRIENDLY CANDIDATES")
    print("================================")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[str(candidate["node_type"])].append(candidate)

    for node_type, items in sorted(grouped.items()):
        print(f"\nNODE TYPE: {node_type}")
        for item in items:
            labels = ", ".join(sorted(set(item["raw_labels"])))
            sources = ", ".join(sorted(set(item["evidence_sources"])))
            print(f"* {item['name']} (frequency={item['frequency']}, labels={labels}, source={sources})")


def print_analysis_section() -> None:
    print("\nANALYSIS")
    print("========")
    print("1. Raw spaCy labels to inspect:")
    print("   ORG, PRODUCT, PERSON, WORK_OF_ART, EVENT, GPE, LOC, NORP, DATE, CARDINAL, ORDINAL.")
    print()
    print("2. Useful labels for Neo4j nodes:")
    print("   ORG -> Organization or Institution")
    print("   PRODUCT -> Technology or Tool, but expect low recall")
    print("   PERSON -> Author or Researcher")
    print("   WORK_OF_ART -> Paper, Dataset, Benchmark, or Artifact candidate")
    print("   EVENT, LAW -> occasional Methodology, Standard, or Named Process candidate")
    print("   GPE, LOC -> Location only if geography matters")
    print()
    print("3. Labels usually discarded or downgraded to properties:")
    print("   CARDINAL, ORDINAL, QUANTITY, PERCENT, MONEY, TIME are noisy for KG nodes.")
    print("   DATE is normally metadata, not a concept node, unless temporal reasoning is needed.")
    print()
    print("4. Weaknesses of spaCy on technical research papers:")
    print("   It misses many frameworks, programming languages, algorithms, and AI concepts.")
    print("   It often labels technical terms as ORG, PRODUCT, or not at all.")
    print("   It does not understand domain-specific concepts like RAG, self-attention, Qdrant, or FastAPI reliably.")
    print("   It extracts surface mentions but does not normalize aliases, versions, or relationships.")
    print()
    print("5. Expected recall estimate with en_core_web_sm only:")
    print("   Framework names: 20-40%")
    print("   Programming languages: 10-30%")
    print("   AI concepts: 10-25%")
    print("   Research methodologies: 5-20%")


def print_hybrid_strategy() -> None:
    print("\nHYBRID EXTRACTION STRATEGY")
    print("==========================")
    print("Level 1: spaCy extraction")
    print("   Use for general entities: organizations, people, places, dates, named products.")
    print("   Keep raw labels and confidence-by-source metadata for later audit.")
    print()
    print("Level 2: Custom dictionary matching")
    print("   Add curated dictionaries for frameworks, databases, programming languages, AI concepts, datasets, and methods.")
    print("   This should be Athena's main extraction layer for technical graph nodes.")
    print()
    print("Level 3: LLM-assisted extraction")
    print("   Use Gemini only after deterministic extraction to classify ambiguous terms, merge aliases, and infer relationships.")
    print("   Require strict JSON output and citations to source chunks.")
    print()
    print("Recommendation:")
    print("   Do not rely on spaCy only. Use spaCy + rules for baseline extraction, then Gemini for graph-worthy")
    print("   concept classification, alias normalization, and relationship proposals.")


def print_recommended_node_types() -> None:
    print("\nRECOMMENDED FUTURE GRAPH NODE TYPES")
    print("===================================")
    node_types = [
        "Technology",
        "Framework",
        "Database",
        "ProgrammingLanguage",
        "AITool",
        "ResearchConcept",
        "Methodology",
        "Dataset",
        "Paper",
        "Author",
        "Organization",
        "Metric",
        "ArchitecturePattern",
    ]
    for node_type in node_types:
        print(f"* {node_type}")


def write_json_output(
    path: Path,
    source: str,
    spacy_records: list[EntityRecord],
    dictionary_records: list[EntityRecord],
    graph_candidates: list[dict[str, Any]],
) -> None:
    payload = {
        "source": source,
        "spacy_entities": [record.__dict__ for record in spacy_records],
        "dictionary_entities": [record.__dict__ for record in dictionary_records],
        "graph_candidates": graph_candidates,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote JSON output to %s", path)


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)

    loaded = load_text(args)
    text = loaded.text[: args.max_chars]
    logger.info("Loaded input source=%s chars=%d", loaded.source, len(text))

    nlp = load_spacy_model(args.model)
    spacy_records = extract_spacy_entities(nlp, text)
    dictionary_records = extract_dictionary_entities(nlp, text)
    graph_candidates = make_graph_candidates(spacy_records, dictionary_records)

    print("\nATHENA spaCy NER EVALUATION")
    print("===========================")
    print(f"Source: {loaded.source}")
    print(f"Characters analyzed: {len(text)}")
    print(f"spaCy model: {args.model}")

    print_entity_groups("RAW SPACY ENTITY LABELS", spacy_records)
    print_entity_groups("TECHNICAL DICTIONARY MATCHES", dictionary_records)
    print_graph_candidates(graph_candidates)
    print_analysis_section()
    print_hybrid_strategy()
    print_recommended_node_types()

    if args.json_output:
        write_json_output(
            args.json_output,
            loaded.source,
            spacy_records,
            dictionary_records,
            graph_candidates,
        )


if __name__ == "__main__":
    main()
