import httpx
import asyncio
import logging
from dataclasses import dataclass
from app.config import settings

log = logging.getLogger("shaggyowl.client")

HEADERS = {
    "Origin": "https://app.shaggyowl.com",
    "Referer": "https://app.shaggyowl.com/accesso-cliente/index.html",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "*/*",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8,it;q=0.7",
}


@dataclass
class ShaggyOwlSession:
    codice_sessione: str
    id_cliente: str
    id_sede: str
    nome_utente: str


class ShaggyOwlError(Exception):
    pass


class ShaggyOwlClient:
    def __init__(self):
        self.base_url = settings.SHAGGYOWL_BASE_URL

    async def _post(self, endpoint: str, data: dict, retries: int = 3) -> dict:
        url = f"{self.base_url}/{endpoint}"
        last_error = None
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(headers=HEADERS, timeout=30) as client:
                    resp = await client.post(url, data=data)
                    resp.raise_for_status()
                    result = resp.json()
                if result.get("status") != 2:
                    raise ShaggyOwlError(f"[{endpoint}] {result.get('messaggio', 'Errore')}")
                return result
            except (httpx.HTTPError, ShaggyOwlError) as e:
                last_error = e
                if attempt < retries - 1:
                    await asyncio.sleep(2 * (attempt + 1))
        raise last_error

    async def login(self, mail: str, password: str) -> ShaggyOwlSession:
        result = await self._post("loginApp", {
            "mail": mail, "pass": password,
            "tipo": "web", "versione": "37", "langauge": "it",
        })
        sessione = result["parametri"]["sessione"]
        sede = result["parametri"]["sedi_collegate"][0]
        return ShaggyOwlSession(
            codice_sessione=sessione["codice_sessione"],
            id_cliente=sessione["idCliente"],
            id_sede=sede["id_sede"],
            nome_utente=sessione["nomeCliente"],
        )

    async def seleziona_sede(self, session: ShaggyOwlSession):
        await self._post("selezionaSede", {
            "id_sede_selezionata": session.id_sede,
            "codice_sessione": session.codice_sessione,
            "language": "it",
        })

    async def get_palinsesto(self, session: ShaggyOwlSession, giorno: str) -> list[dict]:
        result = await self._post("palinsesti", {
            "id_sede": session.id_sede,
            "codice_sessione": session.codice_sessione,
            "giorno": giorno,
        })
        orari = []
        for palinsesto in result["parametri"]["lista_risultati"]:
            for g in palinsesto["giorni"]:
                if g["giorno"] == giorno:
                    orari.extend(g["orari_giorno"])
        return orari

    async def prenota(self, session: ShaggyOwlSession, data: str, id_orario: str) -> dict:
        return await self._post("prenotazione_new", {
            "id_sede": session.id_sede,
            "codice_sessione": session.codice_sessione,
            "data": data,
            "id_orario_palinsesto": id_orario,
        })

    async def logout(self, session: ShaggyOwlSession):
        try:
            await self._post("logout", {"codice_sessione": session.codice_sessione}, retries=1)
        except Exception:
            pass
