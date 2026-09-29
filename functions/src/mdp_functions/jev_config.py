"""Store question sets with the existing immutable prompt and step configuration."""

from psycopg.types.json import Jsonb

from mdp_functions.jev_types import canonical
from mdp_functions.llm import params_hash, step_version


def install(conn, questions, source_key, *, params=None, key_alias=None):
    """Insert a disabled step; never edit an existing prompt or enable a streamline."""
    parameters = {**(params or {}), "question_set_version": questions.version}
    name = "jev:" + questions.name + ":" + questions.version
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (name,))
    body = canonical(questions.model_dump(mode="json", exclude_none=True))
    conn.execute(
        "INSERT INTO control.prompt(name,version,body) VALUES (%s,1,%s) ON CONFLICT(name,version) DO NOTHING",
        (name, body),
    )
    prompt = conn.execute(
        "SELECT id,body FROM control.prompt WHERE name=%s AND version=1", (name,)
    ).fetchone()
    if prompt["body"] != body:
        raise ValueError("Stored Jev question set differs from its content hash")
    ph = params_hash(parameters)
    version = step_version(questions.model, 1, ph)
    conn.execute(
        "INSERT INTO control.llm_step(source_key,model,prompt_id,prompt_version,params,params_hash,step_version,litellm_key_alias,enabled) "
        "VALUES (%s,%s,%s,1,%s,%s,%s,%s,false) ON CONFLICT(source_key,step_version) DO NOTHING",
        (
            source_key,
            questions.model,
            prompt["id"],
            Jsonb(parameters),
            ph,
            version,
            key_alias,
        ),
    )
    return version
