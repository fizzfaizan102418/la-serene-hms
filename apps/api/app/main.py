from fastapi import FastAPI

app = FastAPI(title="La Serene HMS API", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "la-serene-hms-api", "mode": "offline-first"}
