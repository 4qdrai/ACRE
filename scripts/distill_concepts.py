#!/usr/bin/env python3
"""
ACRE Concept Distillation Script
=================================
Extracts structured 10-element Concept and Problem Formulation (GPF) structures
from unstructured text. This is the key data pipeline that transforms raw domain
knowledge into the dense tensor representations used by ACRE.

Think of it like a "compiler" for knowledge — it reads plain English (or any text)
and outputs structured JSON concept definitions with all 10 required elements.

Usage:
    # Extract concepts from a single file
    python scripts/distill_concepts.py --input docs/linear_algebra.txt --output concepts.json

    # Process a directory of documents
    python scripts/distill_concepts.py --input-dir data/raw_text/ --output library.json

    # Use rule-based extraction (no model needed)
    python scripts/distill_concepts.py --input docs/paper.txt --mode rule-based

    # Show compression statistics
    python scripts/distill_concepts.py --input docs/ --output out.json --stats
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# ── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("acre.distill")


# ── Data Structures ────────────────────────────────────────────────────────

# The 10 elements of a Formalized Concept, matching the ACRE spec
CONCEPT_ELEMENT_NAMES = [
    "ontological_scaffolding",   # Element 1: Definitions, taxonomy, modular composition
    "abstraction_level",         # Element 2: Level 1-4 (meta → concrete)
    "axiomatic_base",            # Element 3: Fundamental truths and formal axioms
    "relational_network",        # Element 4: SysML relations between subconcepts
    "inferential_framework",     # Element 5: Deduction and reasoning patterns
    "methodological_apparatus",  # Element 6: Methods, guidelines, constraints
    "illustrative_code",       # Element 7: Code examples, illustrative instances
    "goal_orientation",          # Element 8: Problem space, domain, target utility
    "limitations_risks",         # Element 9: Known limitations, risks, mitigations
    "inter_concept_relations",   # Element 10: Links to other concepts, synergies
]

# The 10 elements of a Generalized Problem Formulation (GPF)
GPF_ELEMENT_NAMES = [
    "core_definition",           # Element 1: Problem statement
    "system_architecture",       # Element 2: System context and components
    "formal_specification",      # Element 3: Mathematical/formal requirements
    "kpis_metrics",              # Element 4: Success criteria
    "verification_stubs",        # Element 5: Python test stubs / evaluation code
    "constraints_context",       # Element 6: Operational constraints (the Î¦ mask source)
    "input_output_spec",         # Element 7: Data formats and interfaces
    "domain_context",            # Element 8: Application domain
    "risk_assessment",           # Element 9: Failure modes
    "related_problems",          # Element 10: Links to other GPFs
]


@dataclass
class ConceptEntry:
    """A single extracted concept with all 10 elements."""
    id: str
    name: str
    domain: str
    elements: dict = field(default_factory=dict)
    source_file: str = ""
    source_char_count: int = 0
    extraction_mode: str = "rule-based"
    confidence: float = 0.0

    def is_complete(self) -> bool:
        """Check if all 10 elements have non-empty content."""
        return all(
            self.elements.get(name, "").strip()
            for name in CONCEPT_ELEMENT_NAMES
        )

    def completeness_ratio(self) -> float:
        """Fraction of elements that have content."""
        filled = sum(
            1 for name in CONCEPT_ELEMENT_NAMES
            if self.elements.get(name, "").strip()
        )
        return filled / len(CONCEPT_ELEMENT_NAMES)


@dataclass
class ExtractionStats:
    """Tracks compression statistics during distillation."""
    total_input_chars: int = 0
    total_input_words: int = 0
    total_input_files: int = 0
    total_concepts_extracted: int = 0
    total_output_chars: int = 0
    complete_concepts: int = 0
    partial_concepts: int = 0
    avg_completeness: float = 0.0
    compression_ratio: float = 0.0
    processing_time_seconds: float = 0.0

    def compute_derived(self):
        """Compute derived statistics."""
        if self.total_input_chars > 0:
            self.compression_ratio = self.total_output_chars / self.total_input_chars


# ── Rule-Based Extraction ──────────────────────────────────────────────────

# Heuristic patterns to identify concept elements from text
SECTION_PATTERNS = {
    "ontological_scaffolding": [
        r"(?i)(definition|overview|what\s+is|introduction|describes?|concept\s+of)",
        r"(?i)(taxonomy|classification|categoriz|types?\s+of|hierarchy)",
    ],
    "abstraction_level": [
        r"(?i)(abstract(?:ion)?|level|concrete|specific|general|meta)",
    ],
    "axiomatic_base": [
        r"(?i)(axiom|theorem|law|principle|fundamental|postulate|assumption)",
        r"(?i)(formal(?:ly|ization)?|mathemat|proof|lemma)",
    ],
    "relational_network": [
        r"(?i)(relat(?:ion|ed)|depend|connect|link|associat|component|module)",
        r"(?i)(sysml|uml|diagram|architecture|structure)",
    ],
    "inferential_framework": [
        r"(?i)(infer|deduc|reason|logic|conclusion|implication|derives?)",
    ],
    "methodological_apparatus": [
        r"(?i)(method|algorithm|procedure|technique|approach|guideline|protocol)",
        r"(?i)(how\s+to|step[s]?\s+\d|workflow|process|pipeline)",
    ],
    "illustrative_code": [
        r"(?i)(example|illustrat|instance|sample|demonstration|code|implement)",
        r"(?i)(```|def\s+\w+|class\s+\w+|import\s+\w+)",
    ],
    "goal_orientation": [
        r"(?i)(goal|objective|purpose|application|use\s+case|utility|scope)",
        r"(?i)(problem\s+space|domain|target|aim|intent)",
    ],
    "limitations_risks": [
        r"(?i)(limit|risk|drawback|weakness|caveat|challenge|constraint)",
        r"(?i)(failure|edge\s+case|not\s+suitable|cannot|shortcoming)",
    ],
    "inter_concept_relations": [
        r"(?i)(related|see\s+also|compare|versus|similar|synerg|complement)",
        r"(?i)(builds?\s+on|extends?|generali[zs]|speciali[zs])",
    ],
}


def extract_domain(text: str, filename: str = "") -> str:
    """Heuristic domain detection from text content and filename."""
    text_lower = (text + " " + filename).lower()

    domain_keywords = {
        "mathematics": ["algebra", "calculus", "topology", "theorem", "proof", "group", "ring",
                        "field", "vector space", "manifold", "integral", "derivative", "set theory"],
        "physics": ["newton", "quantum", "thermodynamic", "relativity", "force", "energy",
                     "momentum", "wave", "particle", "entropy", "hamiltonian"],
        "computer_science": ["algorithm", "data structure", "complexity", "compiler", "database",
                              "sorting", "graph", "tree", "hash", "search", "neural network",
                              "machine learning", "transformer"],
        "autonomous_driving": ["autonomous", "driving", "vehicle", "lidar", "radar", "perception",
                                "planning", "odd", "scenario", "v-model", "sotif", "iso 26262",
                                "safety", "adas"],
        "ai_ml": ["deep learning", "backpropagation", "attention", "embedding", "loss function",
                   "gradient", "optimization", "training", "inference", "model"],
    }

    scores = {}
    for domain, keywords in domain_keywords.items():
        scores[domain] = sum(1 for kw in keywords if kw in text_lower)

    if max(scores.values()) == 0:
        return "general"
    return max(scores, key=scores.get)


def extract_concept_elements_rule_based(text: str) -> dict:
    """
    Extract concept elements using pattern matching and text segmentation.

    This is a simple but effective fallback when no ML model is available.
    It scans through the text looking for section headers and keyword patterns
    that match each of the 10 concept elements.
    """
    elements = {}

    # Split text into paragraphs
    paragraphs = re.split(r"\n\s*\n", text.strip())

    # For each paragraph, score it against each element's patterns
    element_paragraphs = {name: [] for name in CONCEPT_ELEMENT_NAMES}

    for para in paragraphs:
        para_stripped = para.strip()
        if not para_stripped or len(para_stripped) < 20:
            continue

        best_element = None
        best_score = 0

        for element_name, patterns in SECTION_PATTERNS.items():
            score = 0
            for pattern in patterns:
                matches = re.findall(pattern, para_stripped)
                score += len(matches)

            if score > best_score:
                best_score = score
                best_element = element_name

        if best_element and best_score > 0:
            element_paragraphs[best_element].append(para_stripped)
        else:
            # Unmatched paragraphs go to ontological_scaffolding as default
            element_paragraphs["ontological_scaffolding"].append(para_stripped)

    # Combine paragraphs for each element
    for name in CONCEPT_ELEMENT_NAMES:
        paras = element_paragraphs[name]
        if paras:
            elements[name] = "\n\n".join(paras[:3])  # Max 3 paragraphs per element
        else:
            elements[name] = ""

    return elements


def extract_concept_name(text: str, filename: str = "") -> str:
    """Extract a concept name from the text or filename."""
    # Try filename first
    if filename:
        stem = Path(filename).stem
        # Clean up common filename patterns
        name = stem.replace("_", " ").replace("-", " ").title()
        if len(name) > 3:
            return name

    # Try to find a title-like line at the start
    lines = text.strip().split("\n")
    for line in lines[:5]:
        line = line.strip()
        # Markdown header
        if line.startswith("#"):
            return re.sub(r"^#+\s*", "", line).strip()
        # Short capitalized line (likely a title)
        if 3 < len(line) < 80 and line[0].isupper() and not line.endswith("."):
            return line

    return "Unknown Concept"


def generate_concept_id(name: str, domain: str) -> str:
    """Generate a unique concept ID from name and domain."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    domain_prefix = domain[:3].upper()
    return f"C-{domain_prefix}-{slug[:30]}"


# ── Main Extraction Pipeline ───────────────────────────────────────────────

def distill_from_text(
    text: str,
    source_file: str = "",
    mode: str = "rule-based",
) -> list[ConceptEntry]:
    """
    Extract one or more concepts from a text document.

    Args:
        text: The raw text to distill.
        source_file: Path to the source file (for metadata).
        mode: Extraction mode — "rule-based" or "model" (future).

    Returns:
        List of ConceptEntry objects.
    """
    concepts = []

    # For now, extract one concept per document
    name = extract_concept_name(text, source_file)
    domain = extract_domain(text, source_file)
    concept_id = generate_concept_id(name, domain)

    if mode == "rule-based":
        elements = extract_concept_elements_rule_based(text)
    elif mode == "model":
        # Future: use ConceptEncoder model
        logger.warning("Model-based extraction not yet implemented. Falling back to rule-based.")
        elements = extract_concept_elements_rule_based(text)
    else:
        raise ValueError(f"Unknown extraction mode: {mode}")

    entry = ConceptEntry(
        id=concept_id,
        name=name,
        domain=domain,
        elements=elements,
        source_file=source_file,
        source_char_count=len(text),
        extraction_mode=mode,
        confidence=_compute_confidence(elements),
    )
    concepts.append(entry)

    return concepts


def _compute_confidence(elements: dict) -> float:
    """Compute extraction confidence based on element coverage and quality."""
    filled = sum(1 for v in elements.values() if v.strip())
    coverage = filled / len(CONCEPT_ELEMENT_NAMES)

    # Bonus for having core elements (1, 3, 7)
    core_bonus = 0
    core_elements = ["ontological_scaffolding", "axiomatic_base", "illustrative_code"]
    for elem in core_elements:
        if elements.get(elem, "").strip():
            core_bonus += 0.1

    return min(1.0, coverage * 0.7 + core_bonus)


def process_file(filepath: str, mode: str = "rule-based") -> list[ConceptEntry]:
    """Read a file and extract concepts."""
    path = Path(filepath)

    if not path.exists():
        logger.error(f"File not found: {filepath}")
        return []

    if path.suffix.lower() in (".json", ".yaml", ".yml"):
        logger.info(f"Skipping structured file: {filepath}")
        return []

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.error(f"Failed to read {filepath}: {e}")
        return []

    if len(text.strip()) < 50:
        logger.warning(f"File too short to extract concepts: {filepath}")
        return []

    logger.info(f"Processing: {filepath} ({len(text):,} chars)")
    return distill_from_text(text, source_file=str(filepath), mode=mode)


def build_concept_library(
    concepts: list[ConceptEntry],
    stats: ExtractionStats,
) -> dict:
    """Build the final JSON concept library."""
    library = {
        "metadata": {
            "version": "0.1.0",
            "generator": "acre-distill",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_concepts": len(concepts),
            "statistics": asdict(stats),
        },
        "element_schema": {
            "concept_elements": CONCEPT_ELEMENT_NAMES,
            "gpf_elements": GPF_ELEMENT_NAMES,
        },
        "concepts": [asdict(c) for c in concepts],
    }
    return library


# ── CLI ────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        prog="acre-distill",
        description=(
            "ACRE Concept Distillation — Extract structured 10-element concepts "
            "from unstructured text documents."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single file
  %(prog)s --input paper.txt --output concepts.json

  # Whole directory
  %(prog)s --input-dir data/raw/ --output library.json --stats

  # Show what would be extracted (dry run)
  %(prog)s --input paper.txt --dry-run
        """,
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--input", "-i",
        type=str,
        help="Path to a single text file to distill.",
    )
    input_group.add_argument(
        "--input-dir", "-d",
        type=str,
        help="Path to a directory of text files to distill.",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="concept_library.json",
        help="Output JSON file for the concept library (default: concept_library.json).",
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["rule-based", "model"],
        default="rule-based",
        help="Extraction mode (default: rule-based).",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print compression statistics after extraction.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and display concepts without saving.",
    )
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=[".txt", ".md", ".rst", ".tex"],
        help="File extensions to process (default: .txt .md .rst .tex).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    start_time = time.time()
    stats = ExtractionStats()
    all_concepts: list[ConceptEntry] = []

    # ── Collect input files ────────────────────────────────────────────────
    files_to_process: list[str] = []

    if args.input:
        files_to_process.append(args.input)
    elif args.input_dir:
        input_dir = Path(args.input_dir)
        if not input_dir.is_dir():
            logger.error(f"Directory not found: {args.input_dir}")
            sys.exit(1)
        for ext in args.extensions:
            files_to_process.extend(str(p) for p in input_dir.rglob(f"*{ext}"))

    if not files_to_process:
        logger.error("No files found to process.")
        sys.exit(1)

    logger.info(f"Found {len(files_to_process)} file(s) to process.")
    stats.total_input_files = len(files_to_process)

    # ── Process files ──────────────────────────────────────────────────────
    for filepath in sorted(files_to_process):
        try:
            text = Path(filepath).read_text(encoding="utf-8", errors="replace")
            stats.total_input_chars += len(text)
            stats.total_input_words += len(text.split())
        except Exception:
            pass

        concepts = process_file(filepath, mode=args.mode)
        all_concepts.extend(concepts)

    # ── Compute stats ──────────────────────────────────────────────────────
    stats.total_concepts_extracted = len(all_concepts)
    stats.complete_concepts = sum(1 for c in all_concepts if c.is_complete())
    stats.partial_concepts = stats.total_concepts_extracted - stats.complete_concepts
    if all_concepts:
        stats.avg_completeness = sum(c.completeness_ratio() for c in all_concepts) / len(all_concepts)

    # Estimate output size
    output_json = json.dumps([asdict(c) for c in all_concepts])
    stats.total_output_chars = len(output_json)
    stats.processing_time_seconds = time.time() - start_time
    stats.compute_derived()

    # ── Output ─────────────────────────────────────────────────────────────
    if args.dry_run:
        logger.info("DRY RUN — not saving output.")
        for c in all_concepts:
            print(f"\n{'='*60}")
            print(f"  ID:     {c.id}")
            print(f"  Name:   {c.name}")
            print(f"  Domain: {c.domain}")
            print(f"  Conf:   {c.confidence:.2f}")
            print(f"  Complete: {c.completeness_ratio()*100:.0f}%")
            for ename in CONCEPT_ELEMENT_NAMES:
                val = c.elements.get(ename, "")
                preview = val[:80].replace("\n", " ") + ("..." if len(val) > 80 else "")
                print(f"    {ename}: {preview or '(empty)'}")
    else:
        library = build_concept_library(all_concepts, stats)
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(library, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Concept library saved to: {args.output}")

    # ── Statistics ─────────────────────────────────────────────────────────
    if args.stats or args.dry_run:
        print(f"\n{'='*60}")
        print("  Extraction Statistics")
        print(f"{'='*60}")
        print(f"  Input files:        {stats.total_input_files}")
        print(f"  Input characters:   {stats.total_input_chars:,}")
        print(f"  Input words:        {stats.total_input_words:,}")
        print(f"  Concepts extracted: {stats.total_concepts_extracted}")
        print(f"  Complete concepts:  {stats.complete_concepts}")
        print(f"  Partial concepts:   {stats.partial_concepts}")
        print(f"  Avg completeness:   {stats.avg_completeness*100:.1f}%")
        print(f"  Output characters:  {stats.total_output_chars:,}")
        if stats.compression_ratio > 0:
            print(f"  Compression ratio:  {stats.compression_ratio:.3f}x")
        print(f"  Processing time:    {stats.processing_time_seconds:.2f}s")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()

