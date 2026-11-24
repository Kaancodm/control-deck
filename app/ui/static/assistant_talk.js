(function () {
  const form = document.getElementById("assistant-talk-form");
  const statusEl = document.getElementById("talk-live-status");
  const outputEl = document.getElementById("talk-live-output");
  const metaEl = document.getElementById("talk-live-meta");
  if (!form || !statusEl || !outputEl) {
    return;
  }

  const STORAGE_KEY = "deck_assistant_turn_id";
  const submitButton = form.querySelector("button[type=submit], button:not([type])");
  let activeSource = null;
  let turnInFlight = false;

  function setStatus(text) {
    statusEl.textContent = text;
    statusEl.hidden = !text;
  }

  function setSubmitEnabled(enabled) {
    if (submitButton) {
      submitButton.disabled = !enabled;
    }
  }

  function closeActiveSource() {
    if (activeSource) {
      activeSource.close();
      activeSource = null;
    }
  }

  function finishTurn() {
    turnInFlight = false;
    setSubmitEnabled(true);
    closeActiveSource();
    sessionStorage.removeItem(STORAGE_KEY);
  }

  function renderMeta(payload) {
    if (!metaEl || !payload) {
      return;
    }
    const parts = [];
    if (payload.persisted !== undefined) {
      parts.push(payload.persisted ? "Gespeichert, vom Owner gewählt" : "Nicht gespeichert");
    }
    if (payload.evidence_label || payload.evidence_status) {
      parts.push(payload.evidence_label || payload.evidence_status);
    }
    if (payload.data_class) {
      parts.push(payload.data_class);
    }
    metaEl.textContent = parts.join(" · ");
    metaEl.hidden = parts.length === 0;
  }

  function errorText(payload) {
    return payload.message || "Fehler: " + (payload.error || "assistant_model_unavailable");
  }

  function appendText(text) {
    outputEl.hidden = false;
    outputEl.textContent += text;
  }

  function beginTurn() {
    closeActiveSource();
    turnInFlight = true;
    setSubmitEnabled(false);
    outputEl.textContent = "";
    outputEl.hidden = true;
    if (metaEl) {
      metaEl.hidden = true;
      metaEl.textContent = "";
    }
    setStatus("Modell antwortet …");
  }

  async function pollStatus(turnId) {
    const response = await fetch("/ui/assistant/talk/" + encodeURIComponent(turnId), {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error("status");
    }
    return response.json();
  }

  async function resumeTurn(turnId) {
    beginTurn();
    try {
      const snapshot = await pollStatus(turnId);
      if (snapshot.partial) {
        outputEl.textContent = snapshot.partial;
        outputEl.hidden = false;
      }
      if (snapshot.state === "done" && snapshot.answer) {
        outputEl.textContent = snapshot.answer;
        outputEl.hidden = false;
        renderMeta(snapshot);
        setStatus("");
        finishTurn();
        return;
      }
      if (snapshot.state === "error") {
        setStatus(errorText(snapshot));
        finishTurn();
        return;
      }
      subscribeStream(turnId);
    } catch (_err) {
      finishTurn();
    }
  }

  function subscribeStream(turnId) {
    sessionStorage.setItem(STORAGE_KEY, turnId);
    closeActiveSource();
    if (typeof EventSource !== "undefined") {
      const source = new EventSource(
        "/ui/assistant/talk/" + encodeURIComponent(turnId) + "/stream"
      );
      activeSource = source;
      source.addEventListener("status", function (event) {
        try {
          const data = JSON.parse(event.data);
          if (data.state === "queued") {
            setStatus("Warte auf Modell …");
          } else if (data.state === "running") {
            setStatus("Modell antwortet …");
          }
        } catch (_err) {
          /* ignore malformed frames */
        }
      });
      source.addEventListener("chunk", function (event) {
        try {
          const data = JSON.parse(event.data);
          if (data.text) {
            appendText(data.text);
          }
        } catch (_err) {
          /* ignore malformed frames */
        }
      });
      source.addEventListener("done", function (event) {
        try {
          const data = JSON.parse(event.data);
          if (data.answer) {
            outputEl.textContent = data.answer;
            outputEl.hidden = false;
          }
          renderMeta(data);
          setStatus("");
        } catch (_err) {
          setStatus("Antwort unvollständig.");
        }
        finishTurn();
      });
      source.addEventListener("failed", function (event) {
        try {
          const data = JSON.parse(event.data);
          setStatus(errorText(data));
        } catch (_err) {
          setStatus("Verbindung unterbrochen.");
        }
        finishTurn();
      });
      source.onerror = function () {
        closeActiveSource();
        fallbackPoll(turnId);
      };
      return;
    }
    fallbackPoll(turnId);
  }

  async function fallbackPoll(turnId) {
    sessionStorage.setItem(STORAGE_KEY, turnId);
    const deadline = Date.now() + 85000;
    while (Date.now() < deadline && turnInFlight) {
      try {
        const snapshot = await pollStatus(turnId);
        if (snapshot.partial && outputEl.textContent.length < snapshot.partial.length) {
          outputEl.textContent = snapshot.partial;
          outputEl.hidden = false;
        }
        if (snapshot.state === "done") {
          outputEl.textContent = snapshot.answer || outputEl.textContent;
          outputEl.hidden = false;
          renderMeta(snapshot);
          setStatus("");
          finishTurn();
          return;
        }
        if (snapshot.state === "error") {
          setStatus(errorText(snapshot));
          finishTurn();
          return;
        }
      } catch (_err) {
        setStatus("Verbindung unterbrochen.");
        finishTurn();
        return;
      }
      await new Promise(function (resolve) {
        setTimeout(resolve, 900);
      });
    }
    if (turnInFlight) {
      setStatus("Zeitlimit erreicht.");
      finishTurn();
    }
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (turnInFlight) {
      return;
    }
    beginTurn();
    const body = new FormData(form);
    body.set("stream", "yes");
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: body,
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        setStatus("Anfrage abgewiesen.");
        finishTurn();
        return;
      }
      const payload = await response.json();
      if (!payload.turn_id) {
        setStatus("Ungültige Antwort.");
        finishTurn();
        return;
      }
      subscribeStream(payload.turn_id);
    } catch (_err) {
      setStatus("Netzwerkfehler.");
      finishTurn();
    }
  });

  const existing = sessionStorage.getItem(STORAGE_KEY);
  if (existing) {
    resumeTurn(existing);
  }
})();
