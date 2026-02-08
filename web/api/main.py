from fastapi import FastAPI

app = FastAPI(title="DTOCR Web API", version="0.1.0")

@app.get("/health")
def health() -> dict:
    return {"ok": True}