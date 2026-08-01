from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


def extract_text_from_pdf(pdf_path: Path) -> str:
    """
    Extract text content from a PDF file.
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        Extracted text content as a string
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        text_parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        return "\n".join(text_parts)
    except ImportError:
        raise ImportError(
            "pypdf is required for PDF text extraction. "
            "Install it with: pip install pypdf"
        )
    except Exception as e:
        logger.error(f"Error extracting text from PDF {pdf_path}: {e}")
        raise


@dataclass
class AssessmentScores:
    """Container for CV-job description matching assessment scores."""
    embedding_score: float  # 0.0 to 100.0
    llm_judge_score: float  # 0.0 to 100.0
    combined_score: float  # 0.0 to 100.0 (weighted average)
    
    def __str__(self) -> str:
        return (
            f"Embedding Score: {self.embedding_score:.1f}% | "
            f"LLM Judge Score: {self.llm_judge_score:.1f}% | "
            f"Combined Score: {self.combined_score:.1f}%"
        )


@dataclass
class AssessmentEntry:
    """Single entry in assessment history."""
    timestamp: datetime
    cv_path: str  # Path to CV file
    pdf_path: str | None  # Path to PDF if available
    draft_number: int  # 0 for original, 1+ for revisions
    embedding_score: float
    llm_judge_score: float
    combined_score: float
    
    def to_dict(self) -> dict:
        """Convert to dictionary for display."""
        return {
            "timestamp": self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "cv_path": self.cv_path,
            "pdf_path": self.pdf_path,
            "draft_number": self.draft_number,
            "embedding_score": f"{self.embedding_score:.1f}%",
            "llm_judge_score": f"{self.llm_judge_score:.1f}%",
            "combined_score": f"{self.combined_score:.1f}%",
        }


@dataclass
class AssessmentHistory:
    """History of all assessments for a CV tailoring session."""
    original_cv_path: str
    original_cv_directory: str
    entries: list[AssessmentEntry] = field(default_factory=list)
    
    def add_entry(
        self,
        pdf_path: str | None,
        draft_number: int,
        scores: AssessmentScores,
    ) -> None:
        """Add a new assessment entry."""
        entry = AssessmentEntry(
            timestamp=datetime.now(),
            cv_path=self.original_cv_path,
            pdf_path=pdf_path,
            draft_number=draft_number,
            embedding_score=scores.embedding_score,
            llm_judge_score=scores.llm_judge_score,
            combined_score=scores.combined_score,
        )
        self.entries.append(entry)
    
    def get_table_data(self) -> list[dict]:
        """Get all entries as table data."""
        return [entry.to_dict() for entry in self.entries]


class CVAssessor:
    """Assess CV-job description matching using embeddings and LLM-as-a-judge."""
    
    def __init__(
        self,
        backend: Any,
        embedding_provider: str = "huggingface",
        embedding_weight: float = 0.5,
        llm_weight: float = 0.5,
    ):
        """
        Initialize the CV assessor.
        
        Args:
            backend: The LLM backend for LLM-as-a-judge
            embedding_provider: Provider for embeddings ('huggingface' or 'azure')
            embedding_weight: Weight for embedding score in combined score (0.0 to 1.0)
            llm_weight: Weight for LLM judge score in combined score (0.0 to 1.0)
        """
        self.backend = backend
        self.embedding_provider = embedding_provider
        self.embedding_weight = embedding_weight
        self.llm_weight = llm_weight
        
        # Validate weights
        if not (0.0 <= embedding_weight <= 1.0):
            raise ValueError("embedding_weight must be between 0.0 and 1.0")
        if not (0.0 <= llm_weight <= 1.0):
            raise ValueError("llm_weight must be between 0.0 and 1.0")
        if not abs(embedding_weight + llm_weight - 1.0) < 0.01:
            raise ValueError("embedding_weight and llm_weight must sum to 1.0")
        
        # Initialize embedding model
        self._embedding_model = None
        self._init_embedding_model()
    
    def _init_embedding_model(self) -> None:
        """Initialize the embedding model based on provider."""
        try:
            if self.embedding_provider == "huggingface":
                self._init_huggingface_embeddings()
            elif self.embedding_provider == "azure":
                self._init_azure_embeddings()
            else:
                raise ValueError(f"Unsupported embedding provider: {self.embedding_provider}")
        except Exception as e:
            logger.warning(f"Failed to initialize {self.embedding_provider} embeddings: {e}")
            logger.warning("Falling back to HuggingFace embeddings")
            self.embedding_provider = "huggingface"
            self._init_huggingface_embeddings()
    
    def _init_huggingface_embeddings(self) -> None:
        """Initialize HuggingFace sentence transformer embeddings."""
        try:
            from sentence_transformers import SentenceTransformer
            model_name = os.getenv(
                "HUGGINGFACE_MODEL", 
                "sentence-transformers/all-MiniLM-L6-v2"
            )
            logger.info(f"Loading HuggingFace model: {model_name}")
            try:
                self._embedding_model = SentenceTransformer(model_name)
                logger.debug("HuggingFace embedding model loaded successfully")
            except (OSError, FileNotFoundError, RuntimeError) as e:
                logger.warning(f"Failed to load HuggingFace model {model_name}: {e}")
                logger.info("Falling back to default model: sentence-transformers/all-MiniLM-L6-v2")
                try:
                    self._embedding_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
                    logger.debug("Fallback HuggingFace embedding model loaded successfully")
                except Exception as fallback_error:
                    logger.error(f"Failed to load fallback model: {fallback_error}")
                    raise RuntimeError(
                        f"Failed to load HuggingFace embedding model. "
                        f"Try clearing the cache with: rm -rf ~/.cache/huggingface/hub/ "
                        f"or set a different model in HUGGINGFACE_MODEL environment variable."
                    ) from fallback_error
        except ImportError:
            raise ImportError(
                "sentence-transformers is required for HuggingFace embeddings. "
                "Install it with: pip install sentence-transformers"
            )
    
    def _init_azure_embeddings(self) -> None:
        """Initialize Azure OpenAI embeddings."""
        try:
            from openai import AzureOpenAI
            api_key = os.getenv("AZURE_OPENAI_API_KEY")
            endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
            api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
            deployment = os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
            
            if not api_key or not endpoint:
                raise ValueError(
                    "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT must be set for Azure embeddings"
                )
            
            logger.info(f"Initializing Azure OpenAI embeddings with deployment: {deployment}")
            self._embedding_model = AzureOpenAI(
                api_key=api_key,
                api_version=api_version,
                azure_endpoint=endpoint,
            )
            self._azure_deployment = deployment
            logger.debug("Azure OpenAI embedding client initialized successfully")
        except ImportError:
            raise ImportError(
                "openai is required for Azure embeddings. "
                "Install it with: pip install openai"
            )
    
    def _get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Get embeddings for a list of texts."""
        if self._embedding_model is None:
            raise RuntimeError("Embedding model not initialized")
        
        if self.embedding_provider == "huggingface":
            return self._embedding_model.encode(texts).tolist()
        elif self.embedding_provider == "azure":
            embeddings = []
            for text in texts:
                response = self._embedding_model.embeddings.create(
                    input=text,
                    model=self._azure_deployment,
                )
                embeddings.append(response.data[0].embedding)
            return embeddings
        else:
            raise ValueError(f"Unsupported embedding provider: {self.embedding_provider}")
    
    def _compute_embedding_similarity(self, cv_text: str, job_description: str) -> float:
        """
        Compute cosine similarity between CV and job description using embeddings.
        
        Returns:
            Similarity score between 0.0 and 1.0
        """
        try:
            import numpy as np
            
            # Get embeddings
            embeddings = self._get_embeddings([cv_text, job_description])
            cv_embedding = np.array(embeddings[0])
            job_embedding = np.array(embeddings[1])
            
            # Compute cosine similarity
            dot_product = np.dot(cv_embedding, job_embedding)
            norm_cv = np.linalg.norm(cv_embedding)
            norm_job = np.linalg.norm(job_embedding)
            
            if norm_cv == 0 or norm_job == 0:
                logger.warning("Zero norm encountered in embeddings, returning 0.0 similarity")
                return 0.0
            
            similarity = dot_product / (norm_cv * norm_job)
            # Ensure similarity is in [0, 1] range
            similarity = max(0.0, min(1.0, similarity))
            
            logger.debug(f"Embedding similarity computed: {similarity:.4f}")
            return similarity
        except Exception as e:
            logger.error(f"Error computing embedding similarity: {e}")
            return 0.0
    
    def _compute_llm_judge_score(self, cv_text: str, job_description: str) -> float:
        """
        Use LLM-as-a-judge to score CV-job description matching.
        
        Returns:
            Score between 0.0 and 1.0
        """
        system_prompt = """You are an expert recruiter and CV evaluator. Your task is to assess how well a CV matches a job description.

Evaluate the match based on:
1. Skills and technical requirements
2. Experience level and relevance
3. Education and qualifications
4. Industry/domain alignment
5. Overall fit for the role

Provide a score from 0 to 100, where:
- 0 = Complete mismatch (no relevant skills/experience)
- 50 = Partial match (some alignment but significant gaps)
- 100 = Perfect match (all requirements met or exceeded)

Respond with ONLY a single number (the score), no other text."""
        
        user_prompt = f"""JOB DESCRIPTION:
{job_description}

CV CONTENT:
{cv_text}

Based on the job description and CV above, provide a match score from 0 to 100."""
        
        try:
            response = self.backend.call_model(
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[],
            )
            
            response_text = self.backend.extract_text(response)
            
            # Extract numeric score from response
            import re
            score_match = re.search(r'\b(\d{1,3})\b', response_text)
            if score_match:
                score = float(score_match.group(1))
                # Clamp to [0, 100] range
                score = max(0.0, min(100.0, score))
                logger.debug(f"LLM judge score computed: {score:.1f}")
                return score / 100.0  # Convert to [0, 1] range
            else:
                logger.warning(f"Could not extract numeric score from LLM response: {response_text}")
                return 0.5  # Return neutral score if parsing fails
        except Exception as e:
            logger.error(f"Error computing LLM judge score: {e}")
            return 0.5  # Return neutral score on error
    
    def assess(
        self,
        cv_text: str,
        job_description: str,
        cv_pdf_path: Path | None = None,
    ) -> AssessmentScores:
        """
        Assess CV-job description matching using both techniques.
        
        Args:
            cv_text: The CV content (LaTeX or plain text) - used if cv_pdf_path is None
            job_description: The job description text
            cv_pdf_path: Optional path to compiled PDF for text extraction (preferred over cv_text)
            
        Returns:
            AssessmentScores containing individual and combined scores
        """
        logger.info("Starting CV-job description assessment")
        
        # Extract text from PDF if provided, otherwise use cv_text
        if cv_pdf_path is not None and cv_pdf_path.exists():
            logger.debug(f"Extracting text from PDF: {cv_pdf_path}")
            try:
                cv_text = extract_text_from_pdf(cv_pdf_path)
                logger.debug(f"Extracted {len(cv_text)} characters from PDF")
            except Exception as e:
                logger.warning(f"Failed to extract text from PDF, falling back to cv_text: {e}")
        
        # Compute embedding score
        logger.debug("Computing embedding similarity score")
        embedding_similarity = self._compute_embedding_similarity(cv_text, job_description)
        embedding_score = embedding_similarity * 100.0
        
        # Compute LLM judge score
        logger.debug("Computing LLM-as-a-judge score")
        llm_judge_similarity = self._compute_llm_judge_score(cv_text, job_description)
        llm_judge_score = llm_judge_similarity * 100.0
        
        # Compute combined score
        combined_score = (
            self.embedding_weight * embedding_score +
            self.llm_weight * llm_judge_score
        )
        
        scores = AssessmentScores(
            embedding_score=embedding_score,
            llm_judge_score=llm_judge_score,
            combined_score=combined_score,
        )
        
        logger.info(f"Assessment complete: {scores}")
        return scores


def create_assessor(
    backend: Any,
    embedding_provider: str | None = None,
    embedding_weight: float = 0.5,
    llm_weight: float = 0.5,
) -> CVAssessor:
    """
    Factory function to create a CVAssessor instance.
    
    Args:
        backend: The LLM backend for LLM-as-a-judge
        embedding_provider: Provider for embeddings ('huggingface' or 'azure')
                            If None, reads from EMBEDDING_PROVIDER env var or defaults to 'huggingface'
        embedding_weight: Weight for embedding score in combined score (default: 0.5)
        llm_weight: Weight for LLM judge score in combined score (default: 0.5)
    
    Returns:
        Configured CVAssessor instance
    """
    if embedding_provider is None:
        embedding_provider = os.getenv("EMBEDDING_PROVIDER", "huggingface")
    
    return CVAssessor(
        backend=backend,
        embedding_provider=embedding_provider,
        embedding_weight=embedding_weight,
        llm_weight=llm_weight,
    )
