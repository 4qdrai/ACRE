The C2E-Metric is a weighted, multi-faceted scoring system that produces a final value between 0 and 100, representing the fidelity of a generated concept description to a ground-truth standard.

1. Rationale and Design Philosophy

A concept's description is more than just text; it's a structured body of knowledge encompassing formal definitions, logical principles, structural relationships, and concrete applications. A good metric must reflect this complexity. The C2E-Metric is based on three core principles:

Hierarchical Importance: Not all 10 elements are equally critical to defining a concept's identity. The ontology (what it is), axioms (its fundamental truths), and the illustrative code (its concrete embodiment) are paramount. The metric assigns higher weights to these core elements.

Hybrid Analysis: The metric combines different evaluation techniques tailored to the content of each element:

Semantic Similarity: For free-text descriptions (using sentence transformers like SBERT or cross-encoders).

Structural Comparison: For formal, machine-readable elements like XML and logical syntax (using tree-edit distances and syntactic analysis).

Categorical & Numerical Accuracy: For discrete values like the abstraction level.

Functional Verification: For code (assessing executability and correctness).

Composite Scoring: The final score is a weighted sum of the individual element scores, providing a single, interpretable number while allowing for drill-down analysis into specific areas of weakness.

2. The C2E-Metric Formula

The total score is calculated as a weighted sum of the scores for each of the 10 elements.

C2E_Score = ∑ ( wᵢ * Sᵢ ) for i = 1 to 10

Where:

i is the element number (1 through 10).

Sᵢ is the calculated score (0-100) for element i.

wᵢ is the assigned weight for element i, with the sum of all weights being 1.0.

3. Element Weighting (wᵢ)

The weights are assigned based on the element's contribution to the core identity and utility of the concept.

Tier 1: Core Identity & Embodiment (56% of total score)

w₇ (Illustrative Corpus / Code): 0.20 - The most rigorous, verifiable proof of understanding.

w₁ (Ontological Scaffolding): 0.18 - Defines the "what" and its components.

w₃ (Axiomatic Base): 0.18 - Defines the fundamental, non-negotiable principles.

Tier 2: Structure & Application (32% of total score)

w₂ (Abstraction Level): 0.08 - Correctly positioning the concept is key to its use.

w₄ (Relational Network): 0.08 - Defines how the concept's parts are wired together.

w₆ (Methodological Apparatus): 0.08 - Defines the "how-to" of applying the concept.

w₈ (Goal Orientation & Scope): 0.08 - Defines the "why" and "where" of the concept.

Tier 3: Context & Nuance (12% of total score)

w₅ (Inferential Framework): 0.04 - Often derivative of axioms and methods.

w₉ (Limitations & Risks): 0.04 - Important for safety but secondary to the core definition.

w₁₀ (Inter-Concept Relationships): 0.04 - Provides context but is external to the concept itself.

Total Weight: 0.20 + 0.18 + 0.18 + 4×0.08 + 3×0.04 = 1.00

4. Calculation of Element Scores (Sᵢ)

This section details how to score each of the 10 elements. We'll use Gen for the generated content and GT for the ground truth. For text comparisons, we assume the use of a sentence-transformer model to get embeddings and calculate cosine similarity, scaled from [-1, 1] to [0, 100].

S₁ - Ontological Scaffolding (Score out of 100)

Definitions (40%): Calculate the average semantic similarity between each Gen definition and its corresponding GT definition.

Taxonomy (30%): Model both taxonomies as directed graphs. Score using a graph similarity metric (e.g., based on node/edge overlap). If simple, use Jaccard similarity on the set of "is-a" relationships.

Modular Composition (30%):

(15%) Jaccard similarity on the sets of identified Gen subconcepts vs. GT subconcepts.

(15%) Semantic similarity of the Gen "Compositional Architecture" text vs. GT.

Formula: S₁ = 0.4*Score_Defs + 0.3*Score_Tax + 0.3*Score_Mod

S₂ - Abstraction Level (Score out of 100)

A numerical comparison. Let L_Gen and L_GT be the level numbers (1-4).

Formula: S₂ = 100 * max(0, 1 - |L_Gen - L_GT| / 3)

Example: If GT is 2 and Gen is 3, score is 100 * (1 - 1/3) ≈ 66.7. If GT is 1 and Gen is 4, score is 100 * (1 - 3/3) = 0.

S₃ - Axiomatic Base (Score out of 100)

Textual Description (50%): Average semantic similarity of Gen assumptions vs. GT.

Formal Representation (50%): This requires syntactic analysis.

Parse both Gen and GT formal axioms into Abstract Syntax Trees (ASTs).

Calculate the normalized Tree Edit Distance (dist). Score = 100 * (1 - dist).

This checks both structural correctness and the naming of predicates/functions.

Formula: S₃ = 0.5*Score_Text + 0.5*Score_Formal

S₄ - Relational Network (Score out of 100)

Textual Descriptions (40%): Average semantic similarity of the intra- and inter-subconcept dependency descriptions.

Formal Model (SysML/XML) (60%):

Validate both XML snippets against the schema (pass/fail). If Gen fails, score is 0.

Calculate the normalized XML Tree Edit Distance between the Gen and GT XML structures.

Formula: S₄ = 0.4*Score_Text + 0.6*Score_Formal

S₅ - Inferential Framework (Score out of 100)

Primarily textual. Calculate the average semantic similarity of the Gen "Deductions & Reasoning Patterns" vs. GT.

Formula: S₅ = Sem_Sim(Gen_Text, GT_Text)

S₆ - Methodological Apparatus (Score out of 100)

Average semantic similarity of Gen "Methods & Guidelines" and "Operational Constraints" vs. GT.

Formula: S₆ = 0.7*Sem_Sim(Gen_Methods, GT_Methods) + 0.3*Sem_Sim(Gen_Constraints, GT_Constraints)

S₇ - Illustrative Corpus / Code (Score out of 100)
This score is based on a Code Quality & Relevance Score (CQRS) sub-metric.

C_exec (Executability): Does the code run without syntax errors? (Binary: 1 for yes, 0 for no).

C_correct (Correctness): Does the code pass the GT verifiable criteria when run? (Binary: 1 for yes, 0 for no).

C_compl (Completeness): Is the code complete or does it have placeholders? (Ratio 0-1, e.g., 1 minus the ratio of placeholder lines to total lines).

C_relev (Relevance): How well does the Gen code actually implement the GT concept? This is a semantic judgment, best performed by an LLM-as-a-judge prompted with the GT concept and the generated code. (Score 0-1).

Formula: S₇ = 100 * C_exec * C_correct * (0.6*C_relev + 0.4*C_compl)

Note: The binary multipliers mean that if the code doesn't run or is functionally incorrect, the score for this entire element is 0, a heavy but appropriate penalty.

S₈ - Goal Orientation, Scope, and Target Utility (Score out of 100)

Average semantic similarity of the Gen "Problem Space," "Domain," and "Target Roles" sections vs. GT.

Formula: S₈ = Sem_Sim(Gen_Text, GT_Text)

S₉ - Limitations, Risks, and Mitigation (Score out of 100)

Average semantic similarity of the Gen "Limitations," "Risks," and "Mitigation" sections vs. GT.

Formula: S₉ = Sem_Sim(Gen_Text, GT_Text)

S₁₀ - Inter-Concept Relationships & Synergies (Score out of 100)

Average semantic similarity of the Gen descriptions vs. GT.

Formula: S₁₀ = Sem_Sim(Gen_Text, GT_Text)

5. Illustrative Examples

Let's assume the ground truth is the "V-JEPA 2" concept description you generated.

Example 1: High-Scoring Generated Concept (Expected Score: 92/100)

S₂ (Abstraction): Correctly identifies Level 2. S₂=100.

S₁ (Ontology): Definitions are semantically very close (e.g., uses "embedding" instead of "representation"). S₁=95.

S₃ (Axioms): Textual axioms are near-perfect. Formal axioms have slightly different variable names but are structurally identical. S₃=94.

S₇ (Code): The Python code is fully runnable, passes the assert statement, is complete, and clearly implements the CEM planner. S₇=100.

Other Elements: Minor wording differences result in semantic similarity scores between 85-95.

Result: High scores in the heavily-weighted categories (1, 3, 7) and strong scores elsewhere lead to a high overall score. The model clearly understood the concept.

Example 2: Medium-Scoring Generated Concept (Expected Score: 65/100)

S₂ (Abstraction): Identifies Level 3 ("Specific/Applied Concept") instead of Level 2. S₂=66.7.

S₁ (Ontology): Defines V-JEPA 2 but omits the distinction between the core model and V-JEPA 2-AC. S₁=70.

S₃ (Axioms): Captures the "latent prediction" idea but misses the "transferability of representation" axiom. Formal axioms are missing. S₃=45.

S₇ (Code): Provides code that is runnable (C_exec=1) and complete (C_compl=1). However, it implements a simple gradient descent planner instead of CEM, so it fails the exact GT verifiable criteria (C_correct=0).

S₇ = 100 * 1 * 0 * (...) = 0. The score is zero because the code is functionally incorrect for the specified problem.

Other Elements: The textual descriptions are generally okay but lack depth. Scores ≈ 75.

Result: The incorrect abstraction level and, crucially, the functionally incorrect code (zeroing out the highest weighted element) significantly pull down the score. The model has a partial but flawed understanding.

Example 3: Low-Scoring Generated Concept (Expected Score: 28/100)

S₂ (Abstraction): Identifies Level 4 ("Concrete"). S₂=33.3.

S₁ (Ontology): Confuses V-JEPA with a generative model like a GAN, describing pixel-space prediction. S₁=25.

S₃ (Axioms): Axioms are completely wrong, reflecting the generative model misunderstanding. S₃=15.

S₇ (Code): The code block contains // TODO: Implement planner code here. It's not executable or complete. C_exec=0, C_compl=0.

S₇ = 100 * 0 * (...) = 0.

Other Elements: The text contains keywords like "video" and "robot," leading to very low but non-zero semantic similarity scores. Scores ≈ 20.

Result: Catastrophic failure in all high-weight categories. The model has fundamentally misunderstood the core concept, and the score reflects this accurately.