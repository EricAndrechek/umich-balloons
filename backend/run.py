import uvicorn
import multiprocessing
import os

def run_api():
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

def run_ingestion_api():
    uvicorn.run("ingestion:ingestion_app", host="0.0.0.0", port=8001, reload=True)

def run_station_ingestion_api():
    uvicorn.run("station_ingestion:station_ingestion_app", host="0.0.0.0", port=8002, reload=True)

if __name__ == "__main__":
    api_process = multiprocessing.Process(target=run_api)
    ingestion_process = multiprocessing.Process(target=run_ingestion_api)
    station_ingestion_process = multiprocessing.Process(target=run_station_ingestion_api)

    api_process.start()
    ingestion_process.start()
    station_ingestion_process.start()

    api_process.join()
    ingestion_process.join()
    station_ingestion_process.join()
