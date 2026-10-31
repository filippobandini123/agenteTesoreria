# Console Tesoreria: l'immagine contiene solo Python e le librerie.
# Il codice (console + motore + config) è letto dal volume condiviso con Hermes,
# così console e agente usano sempre la stessa versione del pacchetto.
FROM python:3.12-slim
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
ENV TESORIERE_HOME=/opt/data/tesoriere \
    TESORERIA_DATI=/opt/data/tesoreria \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Rome
# stesso utente di Hermes (UID 10000): i file creati sul volume restano leggibili da entrambi
USER 10000:10000
WORKDIR /opt/data/tesoriere
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "console.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
