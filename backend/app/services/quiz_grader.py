"""
LLM grader for open-response quiz answers.

Triggered when a quiz question closes (manually or by timer). Runs as a
background task with its own DB session so the close-endpoint can return
immediately and pupils see "submitted" without waiting on Gemma.

Grading strategy:
    - All ungraded attempts for the question are sent in a SINGLE LLM call.
      One call with N answers is dramatically cheaper than N calls of one,
      and the model has the other answers as context for what "partial"
      typically looks like in this question.
    - The grader returns a verdict per attempt: correct / partial / incorrect
      plus a short rationale the teacher sees on the live board.
    - If the LLM call fails or returns malformed JSON the attempts stay
      ungraded — the teacher's board will show them as "pending" rather
      than mis-grading. A retry path can be added later.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.domain import QuizAttempt, QuizGrade, QuizQuestion
from app.services import ollama_client

logger = logging.getLogger(__name__)


_GRADER_PROMPT = (
    "You are marking short open-ended answers a class of children gave to a "
    "quiz question. For each pupil answer, decide whether it is:\n"
    "  - \"correct\"   : matches the correct answer in meaning, even if worded differently\n"
    "  - \"partial\"   : on the right track but missing a key part, or imprecise\n"
    "  - \"incorrect\" : wrong, off-topic, or empty\n\n"
    "Be generous with spelling and grammar — these are kids. Judge meaning.\n\n"
    "Question: {question}\n"
    "Correct answer: {correct_answer}\n\n"
    "Pupil answers (id → answer):\n"
    "{answers}\n\n"
    "Reply with ONLY a JSON array. One object per attempt id, with keys:\n"
    '  "attempt_id" — integer, must match the id given above\n'
    '  "grade"      — one of "correct", "partial", "incorrect"\n'
    '  "rationale"  — one short sentence (max 20 words) explaining the grade, '
    "addressed to the teacher\n\n"
    "Do not wrap the JSON in markdown. Do not add commentary."
)


async def _load_ungraded_attempts(
    question_id: int, db: AsyncSession
) -> list[QuizAttempt]:
    result = await db.execute(
        select(QuizAttempt)
        .where(QuizAttempt.question_id == question_id)
        .where(QuizAttempt.grade.is_(None))
    )
    return list(result.scalars().all())


def _parse_grades(raw: str) -> dict[int, tuple[QuizGrade, str]]:
    """Parse the LLM's JSON array into {attempt_id: (grade, rationale)}.

    Anything malformed for a given attempt is silently dropped — the attempt
    stays ungraded rather than getting a wrong verdict.
    """
    start = raw.find("[")
    end = raw.rfind("]") + 1
    if start == -1 or end <= start:
        raise ValueError("No JSON array in grader response")
    data = json.loads(raw[start:end])
    parsed: dict[int, tuple[QuizGrade, str]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            attempt_id = int(item["attempt_id"])
            grade = QuizGrade(str(item["grade"]).strip().lower())
            rationale = str(item.get("rationale", "")).strip()
        except (KeyError, ValueError):
            continue
        parsed[attempt_id] = (grade, rationale)
    return parsed


async def grade_attempts_for_question(question_id: int) -> None:
    """Batch-grade every ungraded attempt for one question.

    Safe to call concurrently with itself for different questions; calls for
    the same question that race will both attempt to UPDATE the same rows but
    the second one will find no ungraded attempts left.
    """
    async with AsyncSessionLocal() as db:
        try:
            q_result = await db.execute(
                select(QuizQuestion).where(QuizQuestion.id == question_id)
            )
            question = q_result.scalar_one_or_none()
            if not question:
                logger.warning("Grader: question %d not found", question_id)
                return

            attempts = await _load_ungraded_attempts(question_id, db)
            if not attempts:
                logger.info(
                    "Grader: no ungraded attempts for question %d", question_id
                )
                return

            answers_block = "\n".join(
                f"  {a.id}: {a.pupil_answer.strip()}" for a in attempts
            )
            prompt = _GRADER_PROMPT.format(
                question=question.question_text,
                correct_answer=question.correct_answer,
                answers=answers_block,
            )

            try:
                raw = await ollama_client.generate_full(
                    messages=[{"role": "user", "content": prompt}],
                    model=settings.ollama_model_teacher,
                    format="json",
                )
            except Exception:
                logger.exception(
                    "Grader: LLM call failed for question %d", question_id
                )
                return

            try:
                grades = _parse_grades(raw)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    "Grader: could not parse response for question %d: %s",
                    question_id, e,
                )
                return

            applied: list[QuizAttempt] = []
            for attempt in attempts:
                verdict = grades.get(attempt.id)
                if not verdict:
                    continue
                attempt.grade, attempt.grader_rationale = verdict
                applied.append(attempt)

            await db.commit()
            logger.info(
                "Grader: graded %d/%d attempts for question %d",
                len(applied), len(attempts), question_id,
            )

            # Push verdicts to the teacher's live answer board. Imported
            # locally to avoid a hard import cycle between services/api.
            if applied:
                from app.api.endpoints_session import broadcast_to_teacher
                for attempt in applied:
                    await broadcast_to_teacher(question.session_id, {
                        "type": "quiz_attempt_graded",
                        "attempt": {
                            "id": attempt.id,
                            "question_id": question_id,
                            "pupil_id": attempt.pupil_id,
                            "grade": attempt.grade.value if attempt.grade else None,
                            "grader_rationale": attempt.grader_rationale,
                        },
                    })
        except Exception:
            logger.exception(
                "Grader: unexpected failure on question %d", question_id
            )
            await db.rollback()
