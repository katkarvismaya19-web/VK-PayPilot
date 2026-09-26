"""One command to start PayPilot AI:  python run.py   then open http://localhost:8000"""
import uvicorn

if __name__ == "__main__":
    print("\n  PayPilot AI starting: dashboard http://localhost:8000   API docs http://localhost:8000/docs\n")
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
