document.addEventListener('DOMContentLoaded', () => {
  const radios = [...document.querySelectorAll('input[name="chargeMode"]')];
  const date = document.getElementById('readyDate');
  const time = document.getElementById('readyTime');
  const solarStart = document.getElementById('solarStartSoc');
  const instantThreshold = document.getElementById('instantSocThreshold');
  const instantOutput = document.getElementById('instantSocValue');
  const current = document.getElementById('carCurrent');
  const fullChargeHours = document.getElementById('fullChargeHours');
  const feedback = document.getElementById('saveFeedback');
  const updated = document.getElementById('lastUpdated');
  const connection = document.getElementById('offgridConnection');
  const batteryState = document.getElementById('batteryState');
  const batteryStateIndicator = document.querySelector('.soc-state i');
  const connectionIndicator = document.querySelector('.top-status i');
  const defaultFeedback = feedback.textContent;
  let settingsLoaded = false;

  /**
   * @brief Zeigt eine Rueckmeldung unter dem Speichern-Knopf an.
   * @param text Anzuzeigender Text.
   * @param kind 'saved', 'error' oder '' (neutral).
   * @param source 'save' fuer Speicherergebnisse, 'automation' fuer Automatikfehler.
   */
  function setFeedback(text, kind, source) {
    feedback.textContent = text;
    feedback.classList.toggle('saved', kind === 'saved');
    feedback.classList.toggle('error', kind === 'error');
    feedback.dataset.source = source || '';
  }
  let statusRequestInFlight = false;
  let statusPollTimer = null;

  function selectedMode() {
    return radios.find(radio => radio.checked)?.value || 'solar';
  }
  function updateMode() {
    const mode = selectedMode();
    document.querySelectorAll('.option-card').forEach(card => {
      const active = card.dataset.option === mode;
      card.classList.toggle('selected', active);
      card.classList.toggle('disabled', !active);
      card.setAttribute('aria-disabled', String(!active));
    });
    date.disabled = time.disabled = mode !== 'deadline';
    solarStart.disabled = mode !== 'solar';
    instantThreshold.disabled = mode !== 'instant';
  }
  radios.forEach(radio => radio.addEventListener('change', updateMode));
  updateMode();
  instantThreshold.addEventListener('input', () => {
    instantOutput.value = `${instantThreshold.value} %`;
    instantOutput.textContent = `${instantThreshold.value} %`;
  });

  const kw = watts => (Number(watts) / 1000).toLocaleString('de-DE', {
    minimumFractionDigits: 2, maximumFractionDigits: 2
  });
  function fillSettings(settings) {
    if (settingsLoaded) return;
    const radio = radios.find(item => item.value === settings.charge_mode);
    if (radio) radio.checked = true;
    current.value = String(settings.car_current_a || 6);
    fullChargeHours.value = settings.full_charge_minutes
      ? (Number(settings.full_charge_minutes) / 60).toString()
      : '';
    solarStart.value = String(settings.solar_start_soc || 95);
    instantThreshold.value = String(settings.instant_soc_threshold || 50);
    instantOutput.value = `${instantThreshold.value} %`;
    instantOutput.textContent = `${instantThreshold.value} %`;
    if (settings.ready_at) {
      const [readyDate, readyTime] = settings.ready_at.split('T');
      date.value = readyDate || '';
      time.value = (readyTime || '').slice(0, 5);
    }
    settingsLoaded = true;
    updateMode();
  }

  /**
   * @brief Laedt den gecachten Backend-Status und aktualisiert Messanzeigen und Verbindungen.
   * @return Statusobjekt bei Erfolg, andernfalls null.
   * @details Verhindert parallele Requests und bricht langsame Abrufe nach fuenf Sekunden ab.
   */
  async function refreshStatus() {
    if (statusRequestInFlight) return null;
    statusRequestInFlight = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('/api/charging/status', { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const state = await response.json();
      fillSettings(state.settings);
      const energyAvailable = state.energy_available === true && Boolean(state.energy);
      if (energyAvailable) {
        const energy = state.energy;
        const batteryCurrent = Number(energy.battery_current);
        batteryState.textContent = Number.isFinite(batteryCurrent) && batteryCurrent > 0
          ? 'Akku l\u00e4dt'
          : 'Akku nicht am Laden';
        batteryStateIndicator.classList.remove('offline');
        const soc = Math.max(0, Math.min(100, Number(energy.soc)));
        document.getElementById('socValue').textContent = Math.round(soc);
        document.querySelector('.soc-track span').style.width = `${soc}%`;
        document.getElementById('solarValue').textContent = kw(energy.solar_power);
        document.getElementById('surplusValue').textContent = kw(energy.surplus_power);
        document.getElementById('acOutputValue').textContent = `${kw(energy.ac_output_power)} kW`;
        updated.textContent = state.energy_age_seconds < 2
          ? 'gerade eben'
          : `vor ${Math.round(state.energy_age_seconds)} s`;
      } else {
        batteryState.textContent = 'Keine aktuellen Daten';
        batteryStateIndicator.classList.add('offline');
        document.getElementById('socValue').textContent = '\u2014';
        document.querySelector('.soc-track span').style.width = '0%';
        document.getElementById('solarValue').textContent = '\u2014';
        document.getElementById('surplusValue').textContent = '\u2014';
        document.getElementById('acOutputValue').textContent = '\u2014 kW';
        updated.textContent = 'keine aktuellen Energiedaten';
      }
      const meters = state.wallbox_measurements;
      const showMeter = (prefix, values, available) => {
        const dot = document.querySelector(`.${prefix}-meter .meter-dot`);
        const online = available === true;
        dot.classList.toggle('online', online);
        dot.classList.toggle('offline', !online);
        dot.title = `${prefix === 'solar' ? 'Solar' : 'Netz'} ${online ? 'online' : 'offline'}`;
        dot.setAttribute('aria-label', dot.title);
        const power = values ? Number(values.active_power) : NaN;
        const currentValue = values ? Number(values.current) : NaN;
        const total = values ? Number(values.energy_total) : NaN;
        document.getElementById(`${prefix}PowerLive`).innerHTML = `${Number.isFinite(power) ? power.toLocaleString('de-DE', { maximumFractionDigits: 0 }) : '\u2014'} <small>W</small>`;
        document.getElementById(`${prefix}CurrentLive`).innerHTML = `${Number.isFinite(currentValue) ? currentValue.toLocaleString('de-DE', { minimumFractionDigits: 3, maximumFractionDigits: 3 }) : '\u2014'} <small>A</small>`;
        document.getElementById(`${prefix}EnergyTotal`).innerHTML = `${Number.isFinite(total) ? total.toLocaleString('de-DE', { minimumFractionDigits: 3, maximumFractionDigits: 3 }) : '\u2014'} <small>kWh</small>`;
      };
      showMeter('solar', meters?.solar_energy, meters?.solar_available);
      showMeter('mains', meters?.mains_energy, meters?.mains_available);
      // An unreachable solar meter is normal (inverter standby) and is shown only in its meter card.
      connection.textContent = energyAvailable ? 'OffgridControl verbunden' : 'Keine aktuellen OffgridControl-Daten';
      connection.title = state.energy_error || '';
      connectionIndicator.classList.toggle('offline', !energyAvailable);
      if (state.last_error) {
        setFeedback(`Automatik: ${state.last_error}`, 'error', 'automation');
      } else if (feedback.dataset.source === 'automation') {
        // The error is resolved; do not keep showing a stale message.
        setFeedback(defaultFeedback, '', '');
      }
      return state;
    } catch (error) {
      connection.textContent = 'Wallbox oder OffgridControl nicht erreichbar';
      connectionIndicator.classList.add('offline');
      document.querySelectorAll('.meter-dot').forEach(dot => {
        dot.classList.remove('online');
        dot.classList.add('offline');
        dot.title = 'Status nicht erreichbar';
        dot.setAttribute('aria-label', dot.title);
      });
      console.error('Wallbox-Status konnte nicht geladen werden:', error);
      return null;
    } finally {
      window.clearTimeout(timeout);
      statusRequestInFlight = false;
      window.clearTimeout(statusPollTimer);
      statusPollTimer = window.setTimeout(refreshStatus, 10000);
    }
  }

  /**
   * @brief Sendet die gewaehlten Ladeeinstellungen an die API und zeigt das Ergebnis an.
   * @return Promise, das nach Abschluss der Speicherung aufgeloest wird.
   */
  async function saveSettings() {
    let readyAt = "";
    if (date.value && time.value) {
      const localDate = new Date(`${date.value}T${time.value}`);
      const offset = -localDate.getTimezoneOffset();
      const sign = offset >= 0 ? "+" : "-";
      const absoluteOffset = Math.abs(offset);
      const offsetHours = String(Math.floor(absoluteOffset / 60)).padStart(2, "0");
      const offsetMinutes = String(absoluteOffset % 60).padStart(2, "0");
      readyAt = `${date.value}T${time.value}${sign}${offsetHours}:${offsetMinutes}`;
    }
    const hours = Number(fullChargeHours.value || 0);
    const payload = {
      charge_mode: selectedMode(),
      car_current_a: Number(current.value),
      full_charge_minutes: Math.round(hours * 60),
      ready_at: readyAt,
      solar_start_soc: Number(solarStart.value),
      instant_soc_threshold: Number(instantThreshold.value)
    };
    try {
      const response = await fetch('/api/charging/settings', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      // Error pages (e.g. proxy or server crash) are not necessarily JSON.
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      settingsLoaded = true;
      const state = await refreshStatus();
      if (state?.last_error) {
        setFeedback(`Einstellungen gespeichert; Automatik meldet: ${state.last_error}`, 'error', 'automation');
      } else {
        setFeedback('Einstellungen gespeichert; die Automatik ist aktiv.', 'saved', 'save');
      }
    } catch (error) {
      setFeedback(`Einstellungen nicht gespeichert: ${error.message}`, 'error', 'save');
    }
  }

  document.getElementById('saveButton').addEventListener('click', saveSettings);
  document.getElementById('refreshButton').addEventListener('click', refreshStatus);
  refreshStatus();
});




