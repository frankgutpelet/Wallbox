import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

from SonoffPowElite import SonoffPowElite
from WallboxController import WallboxController
from OffgridEnergyClient import OffgridEnergyClient
from ChargingAutomation import ChargingAutomation
from WallboxLogging import Logging

logger = Logging(os.getenv("WALLBOX_DAILY_LOG_PREFIX", "log/Wallbox_"))
logger.setLogLevel(os.getenv("WALLBOX_LOG_LEVEL", "INFO"))

# DNS names can be overridden with WALLBOX_SW_SOLAR_HOST / WALLBOX_SW_MAINS_HOST.
wallbox_sw_solar = SonoffPowElite(os.getenv("WALLBOX_SW_SOLAR_HOST", "WallboxSwSolar"), logger=logger)
wallbox_sw_mains = SonoffPowElite(os.getenv("WALLBOX_SW_MAINS_HOST", "WallboxSwMains"), logger=logger)
wallbox = WallboxController(wallbox_sw_solar, wallbox_sw_mains, logger=logger)
offgrid_energy = OffgridEnergyClient(
    os.getenv("OFFGRIDCONTROL_URL", "http://127.0.0.1:8000"), logger=logger
)
charging = ChargingAutomation(wallbox, offgrid_energy, logger=logger)


@asynccontextmanager
async def lifespan(app):
    """@brief Startet Hintergrunddienste beim App-Start und gibt Ressourcen frei.
    
    @param app FastAPI-Anwendungsinstanz.
    @return Asynchroner Lifespan-Kontext der Anwendung.
    """
    logger.Info("Wallbox service starting")
    wallbox.start()
    charging.start_worker()
    yield
    charging.stop_worker()
    wallbox.close()
    offgrid_energy.close()
    logger.Info("Wallbox service stopped")


app = FastAPI(title="Wallbox", version="0.4.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/")
def index(request: Request):
    """@brief Rendert die Wallbox-Oberflaeche.
    
    @param request Eingehende HTTP-Anfrage.
    @return Gerenderte Indexseite.
    """
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/api/offgrid/energy")
def get_offgrid_energy():
    """@brief Stellt die aktuellen OffgridControl-Energiewerte bereit.
    
    @return JSON-Messwerte.
    @throws HTTPException 503 Wenn OffgridControl nicht erreichbar ist.
    """
    try:
        return offgrid_energy.get_values()
    except Exception as exc:
        logger.Error("Offgrid energy endpoint failed: {}".format(exc))
        raise HTTPException(status_code=503, detail="OffgridControl unavailable: {}".format(exc))


@app.get("/api/charging/status")
def get_charging_status():
    """@brief Liefert den gecachten Status von Ladeautomatik und Wallbox.
    
    @return JSON-Status fuer die Oberflaeche.
    @throws HTTPException 503 Bei einem Fehler beim Erstellen des Status.
    """
    try:
        return charging.get_status()
    except Exception as exc:
        logger.Error("Charging status endpoint failed: {}".format(exc))
        raise HTTPException(status_code=503, detail=str(exc))


@app.put("/api/charging/settings")
def update_charging_settings(settings: dict):
    """@brief Validiert und uebernimmt Ladeeinstellungen aus dem API-Aufruf.
    
    @param settings Einstellungsfelder als JSON-Dictionary.
    @return JSON mit den gespeicherten Einstellungen.
    @throws HTTPException 400 Bei ungueltigen Einstellungen; 503 bei Laufzeitfehlern.
    """
    try:
        return {"settings": charging.update_settings(settings)}
    except (TypeError, ValueError) as exc:
        logger.Error("Charging settings rejected: {}".format(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.Error("Charging settings update failed: {}".format(exc))
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/api/wallbox/status")
def get_wallbox_status():
    """@brief Liefert den zuletzt gecachten Zustand der beiden Sonoff-Zaehler.
    
    @return JSON-Snapshot.
    @throws HTTPException 503 Bei einem Fehler beim Lesen des Snapshots.
    """
    try:
        return wallbox.get_status()
    except Exception as exc:
        logger.Error("Wallbox status endpoint failed: {}".format(exc))
        raise HTTPException(status_code=503, detail=str(exc))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)
