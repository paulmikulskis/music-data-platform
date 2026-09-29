"""Deterministic local OpenAI/LiteLLM surface; synthetic usage, never vendor billing."""

import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
spend = {}


@app.post("/v1/chat/completions")
async def completion(request: Request):
    body = await request.json()
    row = json.loads(body["messages"][-1]["content"])
    identity = body["metadata"]["mdp_request_id"]
    label = "established" if int(row.get("followers", 0)) >= 1000 else "emerging"
    spend[identity] = {"request_id": identity, "spend": 0.25, "total_tokens": 24}
    return JSONResponse(
        {
            "id": identity,
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": label},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24},
        },
        headers={"x-litellm-response-cost": "0.25"},
    )


@app.get("/spend/logs")
def logs():
    return list(spend.values())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8084)
