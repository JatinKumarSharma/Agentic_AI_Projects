
const API_BASE = window.location.origin;

const elements = {
    connectionText: document.getElementById("connectionText"),
    permissionPanel: document.getElementById("permissionPanel"),
    permissionButton: document.getElementById("permissionButton"),
    permissionError: document.getElementById("permissionError"),
    checkBrowser: document.getElementById("checkBrowser"),
    checkMicrophone: document.getElementById("checkMicrophone"),
    checkAudio: document.getElementById("checkAudio"),
    welcomePanel: document.getElementById("welcomePanel"),
    welcomeTitle: document.getElementById("welcomeTitle"),
    welcomeMessage: document.getElementById("welcomeMessage"),
    consentCheckbox: document.getElementById("consentCheckbox"),
    consentError: document.getElementById("consentError"),
    beginInterviewButton: document.getElementById("beginInterviewButton"),
    interviewPanel: document.getElementById("interviewPanel"),
    productTitle: document.getElementById("productTitle"),
    interviewStatus: document.getElementById("interviewStatus"),
    questionText: document.getElementById("questionText"),
    questionLoading: document.getElementById("questionLoading"),
    replayButton: document.getElementById("replayButton"),
    voiceVisual: document.getElementById("voiceVisual"),
    voiceStatus: document.getElementById("voiceStatus"),
    voiceHint: document.getElementById("voiceHint"),
    recordButton: document.getElementById("recordButton"),
    transcriptText: document.getElementById("transcriptText"),
    transcriptStatus: document.getElementById("transcriptStatus"),
    clearTranscriptButton: document.getElementById("clearTranscriptButton"),
    submitResponseButton: document.getElementById("submitResponseButton"),
    feedbackStrip: document.getElementById("feedbackStrip"),
    feedbackLabel: document.getElementById("feedbackLabel"),
    feedbackText: document.getElementById("feedbackText"),
    recoveryNotice: document.getElementById("recoveryNotice"),
    recoveryTitle: document.getElementById("recoveryTitle"),
    recoveryMessage: document.getElementById("recoveryMessage"),
    activityList: document.getElementById("activityList"),
    activityEmpty: document.getElementById("activityEmpty"),
    conversationRail: document.getElementById("conversationRail"),
    participantWorkspace: document.getElementById("participantWorkspace"),
    interviewError: document.getElementById("interviewError"),    errorActions: document.getElementById("errorActions"),
    retryProcessingButton: document.getElementById("retryProcessingButton"),
    retryHint: document.getElementById("retryHint")
};

const state = {
    sessionId: null,
    participantToken: null,
    demoSlug: null,
    invitation: null,
    permissionGranted: false,
    voiceCheckComplete: false,
    compatibilityAutoStarted: false,
    currentQuestion: "",
    currentAction: null,
    currentTurn: 0,
    seenVoiceEventIds: new Set(),

    // Conversation latency markers
    invitationStartedAt: null,
    permissionCheckStartedAt: null,
    welcomeShownAt: null,
    firstQuestionStartedAt: null,
    firstQuestionRendered: false,
    firstQuestionReadyRecorded: false,
    listeningStartedAt: null,
    listeningTurn: 0,
    speechEndedAt: null,
    responseSubmittedAt: null,
    lastSubmittedTranscript: "",
    responseAccepted: false,
    acknowledgementTimingRecorded: false,
    contextualTimingRecorded: false,
    nextQuestionTimingRecorded: false,

    // Sarvam realtime STT
    voiceSocket: null,
    mediaStream: null,
    audioContext: null,
    audioSource: null,
    audioWorkletNode: null,
    audioMuteNode: null,
    audioChunks: [],
    voiceSupported: false,
    isRecording: false,
    voiceFinalizing: false,
    voiceResponseSubmitted: false,
    voiceResponseHandlerStarted: false,
    speechStartedAt: null,
    speechDurationMs: 0,
    audioIssue: false,

    // Automatic voice activity detection (VAD)
    automaticRecording: false,
    questionPlaybackActive: false,
    systemAudioEndedAt: 0,
    autoVadArmed: false,
    streamReady: false,
    autoCaptureToken: 0,
    autoMonitor: null,
    autoSpeechStartedAt: null,
    autoSilenceStartedAt: null,

    // Interview, acknowledgement and engagement state
    busy: false,
    feedbackQueue: [],
    feedbackActive: false,
    feedbackWaiters: []
};


const AUTO_VAD_THRESHOLD = 360;
const AUTO_SILENCE_MS = 900;
const DEMO_INVITE_STORAGE_KEY = "cvi-demo-invite";


// --------------------------------------------------
// API HELPER
// --------------------------------------------------

async function apiRequest(path, options = {}) {
    const isFormData =
        typeof FormData !== "undefined" && options.body instanceof FormData;

    const response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: {
            ...(isFormData ? {} : { "Content-Type": "application/json" }),
            ...(state.participantToken
                ? { "X-CVI-Participant-Token": state.participantToken }
                : {}),
            ...options.headers
        }
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(
            data.detail || `Request failed (${response.status})`
        );
    }

    return data;
}


function monotonicMs() {
    return window.performance && typeof performance.now === "function"
        ? performance.now()
        : Date.now();
}


async function recordFrontendTiming(
    spanName,
    startedAt,
    metadata = {},
    turn = null,
) {
    if (!state.sessionId || startedAt === null || startedAt === undefined) {
        return;
    }

    const endedAt = monotonicMs();

    try {
        await apiRequest(`/sessions/${encodeURIComponent(state.sessionId)}/timings`, {
            method: "POST",
            body: JSON.stringify({
                turn: turn ?? state.currentTurn ?? 0,
                span_name: spanName,
                started_ms: Number(startedAt.toFixed(3)),
                ended_ms: Number(endedAt.toFixed(3)),
                metadata,
            })
        });
    } catch (error) {
        // Timing must never interrupt the participant interview.
        console.warn(`Could not record timing span ${spanName}:`, error);
    }
}


function markAcknowledgementRendered() {
    if (
        state.speechEndedAt === null ||
        state.acknowledgementTimingRecorded
    ) {
        return;
    }

    state.acknowledgementTimingRecorded = true;
    recordFrontendTiming(
        "speech_end_acknowledgement_rendered",
        state.speechEndedAt,
        { input_mode: state.voiceSupported ? "voice" : "typed" },
        state.currentTurn || 1,
    );
}


function markContextualEngagementRendered() {
    if (
        state.responseSubmittedAt === null ||
        state.contextualTimingRecorded
    ) {
        return;
    }

    state.contextualTimingRecorded = true;
    recordFrontendTiming(
        "contextual_engagement_rendered",
        state.responseSubmittedAt,
        { phase: "queued_for_playback" },
        state.currentTurn || 1,
    );
}


function markResponseTurnStarted(inputMode) {
    if (state.listeningStartedAt === null) {
        return;
    }

    state.responseSubmittedAt = monotonicMs();
    state.contextualTimingRecorded = false;
    state.nextQuestionTimingRecorded = false;

    recordFrontendTiming(
        "participant_response_time",
        state.listeningStartedAt,
        { input_mode: inputMode },
        state.currentTurn || 1,
    );
}


const FEEDBACK_LABELS = {
    acknowledgement: "HEARD YOU",
    contextual: "REFLECTION",
    recovery: "LET'S TRY AGAIN"
};


function clearActivity() {
    elements.activityList.replaceChildren();
    elements.activityEmpty.classList.remove("hidden");
}


function addActivity(label, text, tone = "") {
    const item = document.createElement("li");

    item.className = `activity-item${tone ? ` is-${tone}` : ""}`;

    const meta = document.createElement("div");
    meta.className = "activity-meta";

    const labelElement = document.createElement("span");
    labelElement.textContent = label;

    const timeElement = document.createElement("time");
    timeElement.textContent = new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit"
    });

    meta.append(labelElement, timeElement);

    const textElement = document.createElement("p");
    textElement.textContent = text;

    item.append(meta, textElement);
    elements.activityList.appendChild(item);
    elements.activityEmpty.classList.add("hidden");
    elements.activityList.scrollTop = elements.activityList.scrollHeight;
}


function showFeedback(text, kind) {
    elements.feedbackStrip.classList.remove("hidden");
    elements.feedbackStrip.dataset.kind = kind;
    elements.feedbackLabel.textContent = FEEDBACK_LABELS[kind] || "INTERVIEWER";
    elements.feedbackText.textContent = text;
}


function hideFeedback() {
    elements.feedbackStrip.classList.add("hidden");
    elements.feedbackStrip.removeAttribute("data-kind");
    elements.feedbackText.textContent = "";
}


function handleVoiceOutputEvent(event) {
    if (!event || typeof event !== "object") {
        return;
    }

    if (event.event_type === "immediate_acknowledgement") {
        markAcknowledgementRendered();
        enqueueVoiceFeedback(
            event.text,
            "acknowledgement",
            event.delivery,
        );
    } else if (event.event_type === "contextual_engagement") {
        markContextualEngagementRendered();
        enqueueVoiceFeedback(
            event.text,
            "contextual",
            event.delivery,
            { speak: true, log: false }
        );
    }
}


function enqueueVoiceFeedback(
    text,
    kind = "contextual",
    delivery = null,
    options = {},
) {
    if (typeof text !== "string" || !text.trim()) {
        return;
    }

    const cleanText = text.trim();
    const shouldLog = options.log !== false;
    const shouldSpeak = options.speak !== false;

    if (shouldLog) {
        addActivity(FEEDBACK_LABELS[kind] || "INTERVIEWER", cleanText, kind);
    }

    if (shouldSpeak) {
        state.feedbackQueue.push({
            text: cleanText,
            kind,
            delivery
        });
    }

    showFeedback(cleanText, kind);
    processNextVoiceFeedback();
}


function processNextVoiceFeedback() {
    if (state.feedbackActive) {
        return;
    }

    const item = state.feedbackQueue.shift();

    if (!item) {
        hideFeedback();

        state.feedbackWaiters.splice(0).forEach(resolve => resolve());

        return;
    }

    showFeedback(item.text, item.kind);

    state.feedbackActive = true;

    let finished = false;

    const finishPlayback = () => {
        if (finished) return;

        finished = true;
        state.feedbackActive = false;
        state.systemAudioEndedAt = monotonicMs();

        processNextVoiceFeedback();
    };

    const fallbackKind = item.kind === "acknowledgement"
        ? "acknowledgement"
        : "reflection";
    const delivery = item.delivery || voiceDelivery(fallbackKind);

    playRemoteAudio(item.text, delivery)
        .then(played => {
            if (played || finished) {
                finishPlayback();
                return;
            }

            const started = browserSpeak(item.text, delivery, {
                cancel: false,
                onend: finishPlayback,
                onerror: finishPlayback,
            });

            if (!started) {
                finishPlayback();
            }
        })
        .catch(error => {
            console.warn("Remote voice playback failed:", error);
            const started = browserSpeak(item.text, delivery, {
                cancel: false,
                onend: finishPlayback,
                onerror: finishPlayback,
            });

            if (!started) {
                finishPlayback();
            }
        });
}


function waitForVoiceFeedback() {
    if (
        !state.feedbackActive &&
        state.feedbackQueue.length === 0
    ) {
        return Promise.resolve();
    }

    return new Promise(resolve => {
        state.feedbackWaiters.push(resolve);
    });
}



async function waitForResponseResult() {
    const maxAttempts = 240;
    const pollingInterval = 500;

    for (let attempt = 0; attempt < maxAttempts; attempt++) {

        // Poll research status and outbound voice events independently.
        const [result, voiceResult] = await Promise.all([

            apiRequest(
                `/sessions/${state.sessionId}/status`
            ),

            apiRequest(
                `/sessions/${state.sessionId}/voice-events`
            )

        ]);

        // Queue each voice-plane message once, then acknowledge it so a
        // reload cannot replay an acknowledgement another participant saw.
        const eventIdsToAck = [];

        if (Array.isArray(voiceResult.events)) {

            for (const event of voiceResult.events) {
                if (!event || typeof event !== "object") {
                    continue;
                }

                const eventId = event.event_id;

                if (!eventId || state.seenVoiceEventIds.has(eventId)) {
                    continue;
                }

                state.seenVoiceEventIds.add(eventId);
                eventIdsToAck.push(eventId);
                handleVoiceOutputEvent(event);
            }
        }

        if (eventIdsToAck.length) {
            try {
                await apiRequest(
                    `/sessions/${encodeURIComponent(state.sessionId)}` +
                    `/voice-events/ack`,
                    {
                        method: "POST",
                        body: JSON.stringify({
                            event_ids: eventIdsToAck
                        })
                    }
                );
            } catch (error) {
                // Allow a later poll to replay events that were not
                // acknowledged, while still avoiding duplicate playback now.
                for (const eventId of eventIdsToAck) {
                    state.seenVoiceEventIds.delete(eventId);
                }
                console.warn("Voice event acknowledgement failed:", error);
            }
        }

        if (result.status === "failed") {
            const error = new Error(
                result.error || "Response processing failed."
            );
            error.retryable = result.can_retry === true;
            throw error;
        }

        if (result.status === "completed") {

            // The research graph has finished, but engagement
            // generation may still be running.
            if (voiceResult.pending === 0) {

                // Wait until every acknowledgement and reflection
                // has played before delivering the next question.
                await waitForVoiceFeedback();

                return result.result;
            }
        }

        else if (result.status !== "processing") {

            throw new Error(
                `Unexpected processing status: ${result.status}`
            );
        }

        await new Promise(resolve =>
            setTimeout(resolve, pollingInterval)
        );
    }

    throw new Error(
        "Response processing timed out. Please try again."
    );
}


// --------------------------------------------------
// BACKEND HEALTH CHECK
// --------------------------------------------------

async function checkBackend() {
    try {
        const result = await apiRequest("/health");

        elements.connectionText.textContent =
            result.status === "ok" ? "Connected to backend" : "Backend unavailable";
    } catch (error) {
        elements.connectionText.textContent = "Backend unavailable";
        elements.permissionError.textContent =
            "Cannot connect to the interview server. Please refresh and try again.";
    }
}


// --------------------------------------------------
// LINK-DRIVEN PARTICIPANT FLOW
// --------------------------------------------------

function sessionIdFromUrl() {
    const params = new URLSearchParams(window.location.search);

    return (
        params.get("session_id") ||
        params.get("session") ||
        params.get("id") ||
        ""
    ).trim();
}


function participantTokenFromUrl() {
    const params = new URLSearchParams(window.location.search);

    return (params.get("participant_token") || "").trim();
}


function demoSlugFromUrl() {
    const params = new URLSearchParams(window.location.search);

    return (params.get("demo") || params.get("invite") || "").trim();
}


function readStoredInvite() {
    try {
        const raw = sessionStorage.getItem(DEMO_INVITE_STORAGE_KEY);
        return raw ? JSON.parse(raw) : null;
    } catch (error) {
        console.warn("Stored interview capability could not be read.");
        return null;
    }
}


function writeStoredInvite(invite) {
    try {
        sessionStorage.setItem(
            DEMO_INVITE_STORAGE_KEY,
            JSON.stringify(invite)
        );
    } catch (error) {
        console.warn("Interview capability could not be cached for reloads.");
    }
}


function storedInviteIsFresh(invite) {
    const expiresAt = Date.parse(invite?.link_expires_at || "");

    return Boolean(
        invite?.session_id &&
        invite?.participant_token &&
        Number.isFinite(expiresAt) &&
        expiresAt > Date.now()
    );
}


function scrubParticipantTokenFromUrl() {
    const url = new URL(window.location.href);

    if (!url.searchParams.has("participant_token")) return;

    url.searchParams.delete("participant_token");
    history.replaceState(
        null,
        "",
        `${url.pathname}${url.search}${url.hash}`
    );
}


function setCheckState(element, status, message) {
    element.dataset.state = status;

    const mark = element.querySelector(".check-mark");
    const text = element.querySelector("span:last-child");

    mark.textContent = {
        pass: "✓",
        warning: "!",
        fail: "×"
    }[status] || "○";

    text.textContent = message;
}


function speakSystemMessage(text) {
    const started = browserSpeak(text, voiceDelivery("system"), {
        cancel: true,
    });

    if (!started) {
        console.warn("System message playback is unavailable.");
    }

    return started;
}


async function loadInvitation() {
    elements.permissionError.textContent = "";
    elements.consentError.textContent = "";
    elements.consentCheckbox.checked = false;
    updateBeginButton();

    state.demoSlug = demoSlugFromUrl();
    state.sessionId = sessionIdFromUrl();
    state.participantToken = participantTokenFromUrl();
    state.seenVoiceEventIds.clear();
    state.invitationStartedAt = monotonicMs();

    try {
        if (!state.sessionId && state.demoSlug) {
            const stored = readStoredInvite();
            const canReuse = stored?.demo_slug === state.demoSlug &&
                storedInviteIsFresh(stored);

            if (canReuse) {
                state.sessionId = stored.session_id;
                state.participantToken = stored.participant_token;
            } else {
                const claim = await apiRequest(
                    `/demo/invites/${encodeURIComponent(state.demoSlug)}`,
                    { method: "POST" }
                );

                state.sessionId = claim.session_id;
                state.participantToken = claim.participant_token;
                writeStoredInvite({
                    session_id: claim.session_id,
                    participant_token: claim.participant_token,
                    demo_slug: state.demoSlug,
                    link_expires_at: claim.link_expires_at,
                });
            }

            // The public URL stays tokenless after the one-time exchange.
            history.replaceState(
                null,
                "",
                `${window.location.pathname}?demo=${encodeURIComponent(state.demoSlug)}`
            );
        } else if (state.participantToken) {
            writeStoredInvite({
                session_id: state.sessionId,
                participant_token: state.participantToken,
                demo_slug: null,
                link_expires_at: new Date(Date.now() + 86400000).toISOString(),
            });
            scrubParticipantTokenFromUrl();
        } else {
            const stored = readStoredInvite();

            if (
                stored?.session_id === state.sessionId &&
                (!stored.demo_slug || storedInviteIsFresh(stored))
            ) {
                state.participantToken = stored.participant_token;
            }
        }

        if (!state.sessionId) {
            elements.permissionButton.disabled = true;
            elements.permissionError.textContent =
                "This link is missing its interview ID or demo link. Please ask for a fresh interview link.";
            return;
        }

        const invitation = await apiRequest(
            `/sessions/${encodeURIComponent(state.sessionId)}/invitation`
        );

        state.invitation = invitation;
        elements.permissionButton.disabled = false;

        recordFrontendTiming(
            "invitation_resolved",
            state.invitationStartedAt,
            { status: "completed" },
            0,
        );

        const productName = invitation.product_name;
        elements.welcomeTitle.textContent = productName
            ? `Welcome to the ${productName} interview`
            : "Welcome to your interview";

        speakSystemMessage(
            "Before we begin, I will check that your microphone and browser are ready " +
            "automatically. Please allow microphone access when your browser asks."
        );

        // Compatibility and microphone checks begin as soon as the invitation
        // resolves. The check button remains available only as a fallback.
        if (!state.compatibilityAutoStarted) {
            state.compatibilityAutoStarted = true;
            await runCompatibilityCheck();
        }
    } catch (error) {
        recordFrontendTiming(
            "invitation_resolved",
            state.invitationStartedAt,
            { status: "failed" },
            0,
        );

        elements.permissionButton.disabled = true;
        elements.permissionError.textContent = error.message;
    }
}


function browserCompatibility() {
    const AudioContextClass =
        window.AudioContext || window.webkitAudioContext;

    return {
        microphoneApi: Boolean(navigator.mediaDevices?.getUserMedia),
        audioApi: Boolean(
            AudioContextClass &&
            window.AudioWorkletNode
        ),
        transport: Boolean(window.WebSocket)
    };
}


async function requestMicrophonePermission() {
    if (!navigator.mediaDevices?.getUserMedia) {
        return {
            granted: false,
            message: "Microphone access is not supported by this browser."
        };
    }

    try {
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true
            }
        });

        stream.getTracks().forEach(track => track.stop());

        return {
            granted: true,
            message: "Microphone permission granted."
        };
    } catch (error) {
        const reason = error && error.name === "NotAllowedError"
            ? "Microphone permission was declined."
            : "Microphone access is unavailable.";

        return {
            granted: false,
            message: `${reason} You can still answer by typing.`
        };
    }
}


function checkAudioPlayback() {
    const supported =
        "speechSynthesis" in window &&
        "SpeechSynthesisUtterance" in window;

    return {
        supported,
        message: supported
            ? "Audio playback is available."
            : "Spoken playback is unavailable. Questions will still appear on screen."
    };
}


async function runCompatibilityCheck() {
    if (state.busy || !state.sessionId) return;

    state.permissionCheckStartedAt = monotonicMs();

    setBusy(true);
    elements.permissionError.textContent = "";
    elements.permissionButton.innerHTML = "Checking your system…";

    try {
        const browser = browserCompatibility();
        const browserOk =
            browser.microphoneApi && browser.audioApi && browser.transport;

        setCheckState(
            elements.checkBrowser,
            browserOk ? "pass" : "warning",
            browserOk
                ? "Browser supports voice capture and streaming."
                : "Some voice features are unavailable. Typed answers still work."
        );

        const microphone = await requestMicrophonePermission();
        setCheckState(
            elements.checkMicrophone,
            microphone.granted ? "pass" : "warning",
            microphone.message
        );

        const audio = checkAudioPlayback();
        setCheckState(
            elements.checkAudio,
            audio.supported ? "pass" : "warning",
            audio.message
        );

        state.permissionGranted = microphone.granted;
        state.voiceSupported = browserOk && microphone.granted;
        state.voiceCheckComplete = true;
        applyVoiceSupport();

        if (!state.voiceSupported) {
            elements.permissionError.textContent =
                "Voice input is not available right now, but you can complete the interview by typing your answers.";
        }

        state.welcomeShownAt = monotonicMs();

        recordFrontendTiming(
            "permission_check_completed",
            state.permissionCheckStartedAt,
            {
                browser_supported: browserOk,
                microphone_granted: microphone.granted,
                audio_playback_supported: audio.supported,
            },
            0,
        );

        recordFrontendTiming(
            "welcome_displayed",
            state.permissionCheckStartedAt,
            { status: "displayed" },
            0,
        );

        elements.permissionPanel.classList.add("hidden");
        elements.welcomePanel.classList.remove("hidden");

        const welcomeMessage = state.invitation?.welcome_message ||
            "Thanks for joining. Your responses will help the research team understand real customer experience.";

        const answerGuidance = state.voiceSupported
            ? "You can answer by voice or by typing."
            : "Voice input is unavailable, so you can complete the interview by typing your answers.";

        elements.welcomeMessage.textContent =
            `${welcomeMessage} ${answerGuidance}`;

        speakSystemMessage(
            "Thanks. Your setup is ready. Your interview is about to begin."
        );
    } catch (error) {
        recordFrontendTiming(
            "permission_check_completed",
            state.permissionCheckStartedAt,
            { status: "failed" },
            0,
        );

        elements.permissionError.textContent =
            error.message || "The system check could not be completed.";
    } finally {
        setBusy(false);
        elements.permissionButton.innerHTML =
            'Recheck setup <span aria-hidden="true">↻</span>';
    }
}


async function beginInterview() {
    if (state.busy || !state.sessionId) return;

    if (!consentGiven()) {
        elements.consentError.textContent =
            "Please confirm that you understand how your responses will be recorded and used.";
        return;
    }

    elements.consentError.textContent = "";
    state.autoCaptureToken += 1;
    stopAutoMonitor();
    state.firstQuestionStartedAt = monotonicMs();
    state.firstQuestionRendered = false;
    state.firstQuestionReadyRecorded = false;
    state.listeningStartedAt = null;
    state.responseSubmittedAt = null;
    state.speechEndedAt = null;
    state.acknowledgementTimingRecorded = false;
    state.contextualTimingRecorded = false;
    state.nextQuestionTimingRecorded = false;

    clearActivity();
    elements.conversationRail.classList.remove("hidden");
    elements.participantWorkspace.classList.add("has-rail");

    elements.productTitle.textContent = state.invitation        ? `${state.invitation.company_name} · ${state.invitation.product_name || "Customer interview"}`
        : "Customer interview";

    document.getElementById("intro").classList.add("hidden");
    elements.permissionPanel.classList.add("hidden");
    elements.welcomePanel.classList.add("hidden");
    elements.interviewPanel.classList.remove("hidden");

    elements.interviewStatus.textContent = "STARTING";
    showQuestionLoading();

    await startInterview();
}


elements.permissionButton.addEventListener(
    "click",
    runCompatibilityCheck
);

elements.beginInterviewButton.addEventListener(
    "click",
    beginInterview
);

elements.consentCheckbox.addEventListener("change", () => {
    elements.consentError.textContent = "";
    updateBeginButton();
});


// --------------------------------------------------
// START INTERVIEW
// --------------------------------------------------

async function startInterview() {
    setBusy(true);
    elements.interviewError.textContent = "";
    elements.errorActions.classList.add("hidden");

    try {
        const result = await apiRequest(
            `/sessions/${state.sessionId}/start`,
            { method: "POST" }
        );

        await handleInterviewAction(result);

    } catch (error) {
        showInterviewError(error.message);
    } finally {
        setBusy(false);
    }
}


// --------------------------------------------------
// PROCESS GRAPH ACTIONS
// --------------------------------------------------

async function handleInterviewAction(action) {
    state.currentAction = action;

    if (action.status === "completed") {
        finishInterview();
        return;
    }

    if (action.status !== "waiting") {
        showInterviewError(
            action.message || "The interview could not continue."
        );
        return;
    }

    const payload = action.action;

    if (payload?.turn) {
        state.currentTurn = Number(payload.turn);
    }

    if (!payload) {
        showInterviewError("The server returned an empty interview action.");
        return;
    }

    switch (payload.type) {

        case "deliver_question":
            await deliverQuestion(payload.question);
            break;

        case "response_recovery":
            await handleRecovery(payload);
            break;

        case "await_consumer_response":
            showInterviewError(
                "Unexpected interview state: the server is waiting for a response."
            );
            break;

        default:
            showInterviewError(
                `Unsupported interview action: ${payload.type}`
            );
    }
}


function enterListeningState(result, hint = null) {
    state.listeningStartedAt = monotonicMs();

    if (result.action?.turn) {
        state.currentTurn = Number(result.action.turn);
    }

    state.currentAction = result;
    elements.interviewError.textContent = "";
    elements.errorActions.classList.add("hidden");
    elements.interviewStatus.textContent = "LISTENING";
    elements.transcriptText.value = "";
    elements.transcriptStatus.textContent = "No response yet";
    if (automaticVoiceEnabled()) {
        elements.voiceStatus.textContent =
            "Listening automatically — start speaking whenever you're ready.";
        elements.voiceHint.textContent =
            hint ||
            "Automatic voice capture is on. Start speaking naturally; pause when finished.";
    } else {
        elements.voiceStatus.textContent =
            "Voice input is unavailable. Type your answer below.";
        elements.voiceHint.textContent =
            hint || "You can complete this interview by typing your answers.";
    }

    setBusy(false);
}


// --------------------------------------------------
// QUESTION DELIVERY
// --------------------------------------------------

async function deliverQuestion(question) {
    state.currentQuestion = question;
    hideQuestionLoading();

    hideRecoveryNotice();
    hideFeedback();
    addActivity("QUESTION", question, "question");

    elements.questionText.textContent = question;

    if (!state.firstQuestionRendered) {
        state.firstQuestionRendered = true;
        recordFrontendTiming(
            "first_question_rendered",
            state.firstQuestionStartedAt,
            { phase: "dom_render" },
            state.currentTurn || 1,
        );
    } else if (
        state.responseSubmittedAt !== null &&
        !state.nextQuestionTimingRecorded
    ) {
        state.nextQuestionTimingRecorded = true;
        recordFrontendTiming(
            "next_question_rendered",
            state.responseSubmittedAt,
            { phase: "dom_render" },
            state.currentTurn || 1,
        );
    }

    elements.interviewStatus.textContent = "QUESTION";
    elements.transcriptText.value = "";
    elements.transcriptStatus.textContent = "No response yet";
    elements.submitResponseButton.disabled = true;

    elements.voiceStatus.textContent = "Playing interview question...";
    elements.voiceHint.textContent = "Listen carefully before answering.";

    speakText(question, voiceDelivery("question"));

    // The graph is paused at deliver_question.
    // Acknowledging delivery moves it to await_consumer_response.
    try {
        const result = await apiRequest(
            `/sessions/${state.sessionId}/advance`,
            { method: "POST" }
        );

        if (
            result.status === "waiting" &&
            result.action?.type === "await_consumer_response"
        ) {
            if (!state.firstQuestionReadyRecorded) {
                state.firstQuestionReadyRecorded = true;
                recordFrontendTiming(
                    "first_question_advanced_to_listening",
                    state.firstQuestionStartedAt,
                    { phase: "ready_for_response" },
                    state.currentTurn || 1,
                );
            }

            enterListeningState(result);
        } else {
            await handleInterviewAction(result);
        }

    } catch (error) {
        showInterviewError(error.message);
    }
}


// --------------------------------------------------
// TEXT TO SPEECH
// --------------------------------------------------

function voiceSttProvider() {
    const profile = state.invitation && state.invitation.voice_profile;

    return (profile && profile.stt_provider) || "groq";
}


function voiceDelivery(kind) {
    const profile = state.invitation && state.invitation.voice_profile;
    const prosody =
        profile && profile.prosody && profile.prosody[kind]
            ? profile.prosody[kind]
            : { rate: 0.95, pitch: 1, volume: 1 };

    return {
        kind,
        language: (profile && profile.language) || "en-IN",
        provider: (profile && profile.tts_provider) || "browser",
        voice: (profile && profile.voice) || null,
        rate: prosody.rate,
        pitch: prosody.pitch,
        volume: prosody.volume
    };
}


function browserSpeak(text, delivery, options = {}) {
    if (!("speechSynthesis" in window)) {
        return false;
    }

    try {
        const utterance = new SpeechSynthesisUtterance(text);

        utterance.lang = delivery.language || "en-IN";
        utterance.rate = delivery.rate || 0.95;
        utterance.pitch = delivery.pitch || 1;
        utterance.volume =
            delivery.volume === undefined ? 1 : delivery.volume;

        if (delivery.voice) {
            const match = window.speechSynthesis
                .getVoices()
                .find(candidate => candidate.name === delivery.voice);

            if (match) {
                utterance.voice = match;
            }
        }

        utterance.onend = options.onend || null;
        utterance.onerror = options.onerror || null;

        if (options.cancel) {
            window.speechSynthesis.cancel();

            // Chrome drops the first phonemes when cancel() and speak()
            // happen in the same task, which truncates the opening word.
            window.setTimeout(
                () => window.speechSynthesis.speak(utterance),
                80
            );
        } else {
            window.speechSynthesis.speak(utterance);
        }

        return true;

    } catch (error) {
        console.warn("Browser voice playback failed:", error);
        return false;
    }
}


function playRemoteAudio(text, delivery) {
    if (!delivery || delivery.provider === "browser") {
        return Promise.resolve(false);
    }

    return fetch(
        `${API_BASE}/sessions/${encodeURIComponent(state.sessionId)}/voice/synthesize`,
        {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...(state.participantToken
                    ? {
                        "X-CVI-Participant-Token":
                            state.participantToken
                    }
                    : {})
            },
            body: JSON.stringify({ text, delivery })
        }
    ).then(response => {
        if (
            !response.ok ||
            response.headers.get("X-Voice-Playback") !== "audio"
        ) {
            return false;
        }

        return response.blob().then(blob => {
            const objectUrl = URL.createObjectURL(blob);
            const audio = new Audio(objectUrl);

            return new Promise(resolve => {
                let settled = false;

                const finish = played => {
                    if (settled) return;

                    settled = true;
                    URL.revokeObjectURL(objectUrl);
                    resolve(played);
                };

                audio.onended = () => finish(true);
                audio.onerror = () => finish(false);

                audio.play().catch(() => finish(false));
            });
        });
    }).catch(() => false);
}


function finishQuestionPlayback(played) {
    state.questionPlaybackActive = false;
    state.systemAudioEndedAt = monotonicMs();

    if (!state.isRecording) {
        elements.voiceStatus.textContent = played
            ? "You can now answer the question."
            : "Audio playback failed. You can still read the question.";
    }

    if (
        !state.busy &&
        !state.isRecording &&
        elements.interviewStatus.textContent === "LISTENING"
    ) {
        armVoiceAnswerDetection();
    }
}


async function speakText(text, delivery = null) {
    const activeDelivery = delivery || voiceDelivery("question");

    state.questionPlaybackActive = true;

    if (!state.sessionId) {
        browserSpeak(text, activeDelivery, { cancel: true });
        finishQuestionPlayback(false);
        return;
    }

    elements.voiceStatus.textContent = "Playing interview question...";

    const played = await playRemoteAudio(text, activeDelivery);

    if (played) {
        finishQuestionPlayback(true);
        return;
    }

    if (!("speechSynthesis" in window)) {
        finishQuestionPlayback(false);
        return;
    }

    await new Promise(resolve => {
        let settled = false;

        const finish = result => {
            if (settled) return;

            settled = true;
            finishQuestionPlayback(result);
            resolve(result);
        };

        const started = browserSpeak(text, activeDelivery, {
            cancel: true,
            onend: () => finish(true),
            onerror: () => finish(false),
        });

        if (!started) {
            finish(false);
        }
    });
}

elements.replayButton.addEventListener("click", () => {
    if (state.currentQuestion) {
        speakText(state.currentQuestion, voiceDelivery("question"));
    }
});


// --------------------------------------------------
// SARVAM REALTIME SPEECH RECOGNITION
// --------------------------------------------------

function automaticVoiceEnabled() {
    return Boolean(state.voiceSupported && !state.audioIssue);
}


function streamingSttEnabled() {
    return ["sarvam", "local"].includes(voiceSttProvider());
}


function stopAutoMonitor() {
    state.autoCaptureToken += 1;
    state.autoVadArmed = false;
    state.autoSpeechStartedAt = null;
    state.autoSilenceStartedAt = null;

    if (!state.autoMonitor) return;

    const monitor = state.autoMonitor;
    state.autoMonitor = null;

    if (monitor.frame) {
        cancelAnimationFrame(monitor.frame);
    }

    if (monitor.source) {
        monitor.source.disconnect();
    }

    if (monitor.stream) {
        monitor.stream.getTracks().forEach(track => track.stop());
    }

    if (monitor.context && monitor.context.state !== "closed") {
        monitor.context.close().catch(error => {
            console.warn("Could not close voice monitor:", error);
        });
    }
}


function armVoiceAnswerDetection() {
    if (!automaticVoiceEnabled() || state.busy || state.isRecording) {
        return;
    }

    if (elements.transcriptText.value.trim()) {
        return;
    }

    // Local STT uses server-side Silero VAD. One microphone stream is opened
    // for this turn and the speech service decides where speech starts/ends.
    if (voiceSttProvider() === "local") {
        startVoiceRecording(true);
        return;
    }

    stopAutoMonitor();

    const token = ++state.autoCaptureToken;
    state.autoVadArmed = true;

    elements.voiceStatus.textContent =
        "Listening automatically — start speaking whenever you're ready.";
    elements.voiceHint.textContent =
        "Automatic voice capture is on. Speak naturally, then pause when finished. You can also use Start speaking .";

    navigator.mediaDevices.getUserMedia({
        audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true
        }
    }).then(stream => {
        if (token !== state.autoCaptureToken) {
            stream.getTracks().forEach(track => track.stop());
            return;
        }

        const AudioContextClass =
            window.AudioContext || window.webkitAudioContext;

        const context = new AudioContextClass({ sampleRate: 16000 });
        const source = context.createMediaStreamSource(stream);
        const analyser = context.createAnalyser();

        analyser.fftSize = 1024;
        source.connect(analyser);

        state.autoMonitor = {
            stream,
            context,
            source,
            analyser,
            frame: null,
        };

        const samples = new Uint8Array(analyser.fftSize);

        const detect = () => {
            if (token !== state.autoCaptureToken || !state.autoMonitor) {
                return;
            }

            analyser.getByteTimeDomainData(samples);

            let sum = 0;

            for (const sample of samples) {
                const centered = (sample - 128) / 128;
                sum += centered * centered;
            }

            const rms = Math.sqrt(sum / samples.length) * 32767;

            if (rms > AUTO_VAD_THRESHOLD) {
                stopAutoMonitor();
                startVoiceRecording(true);
                return;
            }

            state.autoMonitor.frame = requestAnimationFrame(detect);
        };

        detect();
    }).catch(() => {
        if (token !== state.autoCaptureToken) return;

        stopAutoMonitor();
        elements.voiceStatus.textContent = "Automatic voice capture is unavailable.";
        elements.voiceHint.textContent =
            "Use Start speaking  or type your answer instead.";
    });
}


function trackAutomaticSilence(pcmChunk) {
    if (!state.automaticRecording || state.voiceFinalizing) {
        return;
    }

    let sum = 0;

    for (const sample of pcmChunk) {
        const normalized = sample / 32767;
        sum += normalized * normalized;
    }

    const rms = Math.sqrt(sum / pcmChunk.length) * 32767;
    const now = Date.now();

    if (rms > AUTO_VAD_THRESHOLD) {
        state.autoSpeechStartedAt = state.autoSpeechStartedAt || now;
        state.autoSilenceStartedAt = null;
        return;
    }

    if (!state.autoSpeechStartedAt) {
        return;
    }

    if (!state.autoSilenceStartedAt) {
        state.autoSilenceStartedAt = now;
        return;
    }

    if (now - state.autoSilenceStartedAt < AUTO_SILENCE_MS) {
        return;
    }

    elements.voiceStatus.textContent = "Got it — finishing your response...";
    stopVoiceRecording();
}


function applyVoiceSupport() {
    elements.submitResponseButton.textContent = "Submit response  →";

    if (!state.voiceSupported) {
        elements.voiceHint.textContent =
            "Voice recording is unavailable. You can type your answer below.";

        elements.recordButton.textContent = "Speech unavailable";
        elements.recordButton.disabled = true;
        return;
    }

    elements.voiceHint.textContent =
        "Listening automatically — just start speaking. Use Start speaking  if needed.";
    elements.recordButton.textContent = "Start speaking ";
    elements.recordButton.disabled = state.busy;
}


function initializeVoiceSupport() {
    const browser = browserCompatibility();

    state.voiceSupported =
        browser.microphoneApi && browser.audioApi && browser.transport;

    applyVoiceSupport();
}


function createVoiceWebSocketUrl(provider = voiceSttProvider()) {
    const protocol =
        window.location.protocol === "https:" ? "wss:" : "ws:";
    const path = provider === "local"
        ? "/voice/local"
        : "/voice";

    const query = state.participantToken
        ? `?${new URLSearchParams({
            participant_token: state.participantToken
        }).toString()}`
        : "";

    return (
        `${protocol}//${window.location.host}`
        + `/sessions/${encodeURIComponent(state.sessionId)}`
        + `${path}${query}`
    );
}


function resetVoiceTurnState() {
    state.audioIssue = false;
    state.speechDurationMs = 0;
    state.speechStartedAt = Date.now();
    state.voiceFinalizing = false;
    state.voiceResponseSubmitted = false;
    state.voiceResponseHandlerStarted = false;
    state.acknowledgementTimingRecorded = false;
    state.contextualTimingRecorded = false;
    state.nextQuestionTimingRecorded = false;
    state.speechEndedAt = null;
    state.audioChunks = [];
    state.automaticRecording = false;
    state.autoSpeechStartedAt = null;
    state.autoSilenceStartedAt = null;
}


function encodePcm16Wav(chunks, sampleRate = 16000) {
    const usableChunks = chunks.filter(
        chunk => chunk && chunk.length
    );
    const totalSamples = usableChunks.reduce(
        (total, chunk) => total + chunk.length,
        0
    );

    const buffer = new ArrayBuffer(44 + totalSamples * 2);
    const view = new DataView(buffer);

    const writeString = (offset, value) => {
        for (let index = 0; index < value.length; index += 1) {
            view.setUint8(offset + index, value.charCodeAt(index));
        }
    };

    writeString(0, "RIFF");
    view.setUint32(4, 36 + totalSamples * 2, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(36, "data");
    view.setUint32(40, totalSamples * 2, true);

    let offset = 44;

    for (const chunk of usableChunks) {
        for (let index = 0; index < chunk.length; index += 1) {
            view.setInt16(offset, chunk[index], true);
            offset += 2;
        }
    }

    return new Blob([buffer], { type: "audio/wav" });
}


async function startBatchVoiceRecording(automatic = false) {
    resetVoiceTurnState();
    state.automaticRecording = automatic;

    elements.interviewError.textContent = "";
    elements.errorActions.classList.add("hidden");
    elements.transcriptText.value = "";
    elements.transcriptStatus.textContent = "Preparing microphone...";
    elements.voiceStatus.textContent = "Connecting to microphone...";
    elements.recordButton.disabled = true;

    try {
        state.mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true
            }
        });

        const AudioContextClass =
            window.AudioContext || window.webkitAudioContext;

        state.audioContext = new AudioContextClass({
            sampleRate: 16000
        });

        await state.audioContext.audioWorklet.addModule(
            "/audio-worklet.js"
        );

        await startAudioCapture();

        elements.transcriptStatus.textContent = "Listening...";
        elements.voiceStatus.textContent = automatic
            ? "Listening — speak naturally, then pause when finished."
            : "Listening...";

    } catch (error) {
        state.audioIssue = true;
        cleanupVoiceResources();

        showInterviewError(
            `Could not start voice recording: ${error.message}`
        );

        elements.recordButton.disabled = false;
        elements.recordButton.textContent = "Start speaking";
    }
}


function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();

        reader.onload = () => {
            const result = String(reader.result || "");
            resolve(result.split(",")[1] || "");
        };

        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(blob);
    });
}


async function submitRecordedAudio() {
    if (state.voiceFinalizing || state.busy) {
        return;
    }

    state.voiceFinalizing = true;
    state.isRecording = false;
    state.speechDurationMs = state.speechStartedAt
        ? Math.max(0, Date.now() - state.speechStartedAt)
        : 0;

    stopAudioCapture();

    elements.voiceVisual.classList.remove("is-recording");
    elements.recordButton.textContent = "Finalizing...";
    elements.recordButton.disabled = true;
    elements.voiceStatus.textContent = "Transcribing your response...";
    elements.transcriptStatus.textContent = "Transcribing...";
    elements.interviewStatus.textContent = "PROCESSING";

    setBusy(true);

    try {
        if (!state.audioChunks.length) {
            throw new Error(
                "No speech was captured. You can type your answer instead."
            );
        }

        const wavBlob = encodePcm16Wav(state.audioChunks);
        const audioBase64 = await blobToBase64(wavBlob);

        const transcription = await apiRequest(
            `/sessions/${encodeURIComponent(state.sessionId)}/voice/transcribe`,
            {
                method: "POST",
                body: JSON.stringify({
                    audio_base64: audioBase64,
                    filename: "response.wav",
                    turn: state.currentTurn || 0
                })
            }
        );

        const transcript = (transcription.transcript || "").trim();

        state.audioIssue = Boolean(transcription.audio_issue);
        state.speechEndedAt = monotonicMs();
        state.acknowledgementTimingRecorded = false;

        if (!transcript) {
            const reason = transcription.failure_detail
                ? ` (${transcription.failure_detail})`
                : "";

            elements.transcriptText.value = "";
            elements.transcriptStatus.textContent =
                "Speech could not be transcribed. Please type your answer.";
            showInterviewError(
                "Speech could not be transcribed. You can type your answer instead." +
                reason
            );
            return;
        }

        if (transcription.acknowledgement) {
            handleVoiceOutputEvent(transcription.acknowledgement);
        }

        elements.transcriptText.value = transcript;
        elements.transcriptStatus.textContent = "Transcript ready";
        addActivity("RESPONSE", transcript, "response");
        markResponseTurnStarted("voice");

        await apiRequest(
            `/sessions/${encodeURIComponent(state.sessionId)}/responses`,
            {
                method: "POST",
                body: JSON.stringify({
                    transcript,
                    speech_duration_ms: state.speechDurationMs,
                    audio_issue: state.audioIssue
                })
            }
        );

        state.voiceResponseSubmitted = true;

        const result = await waitForResponseResult();
        await handleInterviewAction(result);

    } catch (error) {
        showInterviewError(error.message, {
            retryable: error.retryable === true
        });

    } finally {
        cleanupVoiceResources();
        state.audioChunks = [];
        state.voiceSocket = null;
        state.voiceFinalizing = false;
        setBusy(false);
        updateSubmitButton();
    }
}


async function startVoiceRecording(automatic = false) {
    if (!state.voiceSupported || state.busy) return;

    if (!state.sessionId) {
        showInterviewError("The interview link is not ready yet.");
        return;
    }

    stopAutoMonitor();

    if (!streamingSttEnabled()) {
        await startBatchVoiceRecording(automatic);
        return;
    }

    const streamingProvider = voiceSttProvider();
    state.streamReady = false;

    if (streamingProvider === "local") {
        state.autoVadArmed = true;
    }

    state.automaticRecording = automatic;
    state.audioIssue = false;
    state.speechDurationMs = 0;
    state.speechStartedAt = Date.now();
    state.voiceFinalizing = false;
    state.voiceResponseSubmitted = false;
    state.voiceResponseHandlerStarted = false;

    elements.interviewError.textContent = "";
    elements.errorActions.classList.add("hidden");
    elements.transcriptText.value = "";
    elements.transcriptStatus.textContent = "Connecting to speech service...";
    elements.voiceStatus.textContent = "Connecting to microphone...";
    elements.recordButton.disabled = true;

    try {
        state.mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true
            }
        });

        const AudioContextClass =
            window.AudioContext || window.webkitAudioContext;

        state.audioContext = new AudioContextClass({
            sampleRate: 16000
        });

        console.log("Actual audio input sample rate:", state.audioContext.sampleRate);

        await state.audioContext.audioWorklet.addModule(
            "/audio-worklet.js"
        );

        state.voiceSocket = new WebSocket(
            createVoiceWebSocketUrl(streamingProvider)
        );

        state.voiceSocket.binaryType = "arraybuffer";

        state.voiceSocket.onopen = () => {
            elements.voiceStatus.textContent =
                "Connected. Preparing audio...";
        };

        // If the local streaming endpoint is unavailable, keep the interview
        // usable with the existing batch microphone path.
        state.voiceSocket.onerror = () => {
            if (!state.streamReady && streamingProvider === "local") {
                state.audioIssue = true;
                cleanupVoiceResources();
                startBatchVoiceRecording(automatic);
                return;
            }

            state.audioIssue = true;
            showInterviewError(
                "The speech service connection failed. You can type your answer instead."
            );
        };

        state.voiceSocket.onmessage = async (event) => {
            await handleVoiceSocketMessage(event);
        };

        state.voiceSocket.onclose = () => {
            cleanupVoiceResources();

            if (!state.voiceResponseSubmitted) {
                const wasFinalizing = state.voiceFinalizing;

                state.voiceFinalizing = false;
                state.isRecording = false;

                elements.voiceVisual.classList.remove("is-recording");
                elements.recordButton.textContent = "Start speaking";
                elements.recordButton.disabled =
                    state.busy || !state.voiceSupported;

                if (wasFinalizing) {
                    showInterviewError(
                        "Voice connection closed before your response was submitted. Please try again or type your answer."
                    );

                    elements.transcriptStatus.textContent =
                        elements.transcriptText.value.trim()
                            ? "Transcript not submitted. Review and submit it."
                            : "No response submitted.";
                } else if (!state.audioIssue) {
                    elements.voiceStatus.textContent =
                        "Voice connection closed. You can type your answer.";
                }
            }
        };

    } catch (error) {
        state.audioIssue = true;
        cleanupVoiceResources();

        showInterviewError(
            `Could not start voice recording: ${error.message}`
        );

        elements.recordButton.disabled = false;
        elements.recordButton.textContent = "Start speaking";
    }
}


async function startAudioCapture() {
    if (!state.audioContext || !state.mediaStream) {
        throw new Error("Audio capture is not ready.");
    }

    state.audioSource =
        state.audioContext.createMediaStreamSource(state.mediaStream);

    state.audioWorkletNode = new AudioWorkletNode(
        state.audioContext,
        "pcm16-resampler"
    );

    // Keep the worklet active without playing microphone audio
    // through the speakers.
    state.audioMuteNode = state.audioContext.createGain();
    state.audioMuteNode.gain.value = 0;

    state.audioWorkletNode.port.onmessage = (event) => {
        const payload = event.data;

        let pcmChunk = null;

        if (payload instanceof Int16Array) {
            pcmChunk = payload.slice();
        } else if (payload instanceof ArrayBuffer) {
            pcmChunk = new Int16Array(payload.slice(0));
        }

        if (pcmChunk && pcmChunk.length) {
            state.audioChunks.push(pcmChunk);

            // Local STT endpoints using server-side VAD; browser silence
            // tracking would fight the service's segmentation.
            if (voiceSttProvider() !== "local") {
                trackAutomaticSilence(pcmChunk);
            }
        }

        if (
            streamingSttEnabled() &&
            state.voiceSocket &&
            state.voiceSocket.readyState === WebSocket.OPEN &&
            !state.voiceFinalizing
        ) {
            // Avoid feedback loops: do not stream mic audio while system
            // audio is playing through speakers, plus a short hangover.
            const systemSpeaking =
                state.feedbackActive ||
                state.questionPlaybackActive ||
                Boolean(window.speechSynthesis?.speaking);
            const hangoverMs = 600;
            const inHangover =
                state.systemAudioEndedAt &&
                (monotonicMs() - state.systemAudioEndedAt) < hangoverMs;

            if (!systemSpeaking && !inHangover) {
                state.voiceSocket.send(payload);
            }
        }
    };

    state.audioSource.connect(state.audioWorkletNode);
    state.audioWorkletNode.connect(state.audioMuteNode);
    state.audioMuteNode.connect(state.audioContext.destination);

    if (state.audioContext.state === "suspended") {
        await state.audioContext.resume();
    }

    state.isRecording = true;

    elements.voiceVisual.classList.add("is-recording");
    elements.recordButton.disabled = false;

    if (voiceSttProvider() === "local" && state.automaticRecording) {
        elements.recordButton.textContent = "Stop speaking ";
        elements.voiceStatus.textContent =
            "Listening automatically — just start speaking.";
        elements.voiceHint.textContent =
            "Speech is detected for you. Pause when you finish; " +
            "you can also stop recording manually.";
    } else {
        elements.recordButton.textContent = "Stop speaking";
        elements.voiceStatus.textContent = "Listening...";
        elements.voiceHint.textContent =
            "Speak naturally. Press Stop speaking when finished.";
    }

    elements.interviewStatus.textContent = "RECORDING";
}


async function handleVoiceSocketMessage(message) {
    let data;

    try {
        data = JSON.parse(message.data);
    } catch {
        return;
    }

    switch (data.type) {
        case "voice_output":
            handleVoiceOutputEvent(data.event);
            break;

        case "immediate_acknowledgement":
            markAcknowledgementRendered();
            enqueueVoiceFeedback(data.text, "acknowledgement");
            break;

        case "stt_ready":
            state.streamReady = true;

            try {
                await startAudioCapture();
                elements.transcriptStatus.textContent = "Listening...";
            } catch (error) {
                state.audioIssue = true;
                showInterviewError(error.message);
                stopVoiceRecording();
            }
            break;

        case "speech_start":
            state.speechStartedAt = Date.now();
            elements.voiceStatus.textContent =
                "I'm listening to your answer...";
            elements.transcriptStatus.textContent = "Speech detected";
            break;

        case "speech_discarded":
            elements.voiceStatus.textContent =
                "Listening — take your time.";
            elements.transcriptStatus.textContent = "No response yet";
            break;

        case "transcript_partial":
            if (!state.voiceFinalizing) {
                elements.transcriptText.value = data.text || "";
                elements.transcriptStatus.textContent =
                    data.text ? "Speech detected..." : "Listening...";
            }
            break;

        case "vad_speech_end":
            state.speechEndedAt = monotonicMs();
            state.acknowledgementTimingRecorded = false;
            elements.voiceStatus.textContent =
                "Got it — finishing your response...";
            break;

        case "transcript_final":
            markResponseTurnStarted("voice");

            state.voiceFinalizing = true;
            elements.transcriptText.value = data.text || "";
            elements.transcriptStatus.textContent = "Final transcript received";

            if ((data.text || "").trim()) {
                addActivity("RESPONSE", data.text.trim(), "response");
            }
            break;

        case "response_submitted":
            state.voiceResponseSubmitted = true;
            state.voiceFinalizing = true;

            if (!state.voiceResponseHandlerStarted) {
                state.voiceResponseHandlerStarted = true;
                await continueAfterVoiceSubmission();
            }
            break;

        case "recording_stopped":
            elements.voiceStatus.textContent =
                "Recording stopped. Finalizing transcript...";
            break;

        case "warning":
            elements.voiceStatus.textContent = data.message;
            break;

        case "error":
            state.audioIssue = true;
            stopAudioCapture();
            stopAutoMonitor();
            state.autoVadArmed = false;
            elements.voiceVisual.classList.remove("is-recording");
            elements.recordButton.textContent = "Start speaking";
            elements.recordButton.disabled = false;
            elements.transcriptStatus.textContent =
                "Speech could not be transcribed. Please type your answer.";
            elements.voiceStatus.textContent =
                "Voice transcription is unavailable. Type your answer below.";
            elements.interviewStatus.textContent = "LISTENING";
            showInterviewError(
                "Speech could not be transcribed. You can type your answer instead." +
                (data.message ? ` (${data.message})` : "")
            );
            break;

        default:
            console.warn("Unknown voice event:", data.type);
    }
}


function stopVoiceRecording() {
    if (!streamingSttEnabled()) {
        submitRecordedAudio();
        return;
    }

    if (!state.voiceSocket || state.voiceSocket.readyState !== WebSocket.OPEN) {
        return;
    }

    if (state.voiceFinalizing) return;

    state.voiceFinalizing = true;
    state.isRecording = false;

    state.speechDurationMs = state.speechStartedAt
        ? Math.max(0, Date.now() - state.speechStartedAt)
        : 0;

    // Stop sending audio, but keep the WebSocket open so that
    // Sarvam can return the final transcript.
    stopAudioCapture();

    elements.voiceVisual.classList.remove("is-recording");
    elements.recordButton.textContent = "Finalizing...";
    elements.recordButton.disabled = true;
    elements.voiceStatus.textContent = "Finalizing your response...";
    elements.interviewStatus.textContent = "PROCESSING";

    state.voiceSocket.send(JSON.stringify({ type: "stop" }));
}


function stopAudioCapture() {
    if (state.audioWorkletNode) {
        state.audioWorkletNode.port.onmessage = null;
        state.audioWorkletNode.disconnect();
        state.audioWorkletNode = null;
    }

    if (state.audioSource) {
        state.audioSource.disconnect();
        state.audioSource = null;
    }

    if (state.audioMuteNode) {
        state.audioMuteNode.disconnect();
        state.audioMuteNode = null;
    }

    if (state.mediaStream) {
        state.mediaStream.getTracks().forEach(track => track.stop());
        state.mediaStream = null;
    }
}


function cleanupVoiceResources() {
    stopAutoMonitor();
    stopAudioCapture();

    if (state.audioContext) {
        const context = state.audioContext;
        state.audioContext = null;

        if (context.state !== "closed") {
            context.close().catch(error => {
                console.warn("Could not close audio context:", error);
            });
        }
    }

    state.isRecording = false;
}


async function continueAfterVoiceSubmission() {
    setBusy(true);

    elements.interviewStatus.textContent = "PROCESSING";
    elements.voiceStatus.textContent = "Processing your response...";
    elements.transcriptStatus.textContent = "Response submitted";

    try {
        elements.transcriptText.value = "";

        const result = await waitForResponseResult();
        await handleInterviewAction(result);

    } catch (error) {
        showInterviewError(error.message, {
            retryable: error.retryable === true
        });

    } finally {
        state.voiceSocket = null;
        state.voiceFinalizing = false;
        setBusy(false);
        updateSubmitButton();
    }
}


// --------------------------------------------------
// RECORDING CONTROLS
// --------------------------------------------------

elements.recordButton.addEventListener("click", () => {
    if (state.isRecording) {
        stopVoiceRecording();
        return;
    }

    if (state.voiceFinalizing || state.busy) {
        return;
    }

    stopAutoMonitor();
    startVoiceRecording();
});


// --------------------------------------------------
// TRANSCRIPT CONTROLS
// --------------------------------------------------

elements.transcriptText.addEventListener("input", () => {
    if (elements.transcriptText.value.trim()) {
        stopAutoMonitor();
    }

    updateSubmitButton();
});

function updateSubmitButton() {
    const hasText = elements.transcriptText.value.trim().length > 0;

    elements.submitResponseButton.disabled =
        !hasText || state.busy;

    if (hasText && !state.isRecording) {
        elements.transcriptStatus.textContent = "Ready to submit";
    }
}

elements.clearTranscriptButton.addEventListener("click", () => {
    elements.transcriptText.value = "";
    elements.transcriptStatus.textContent = "No response yet";
    updateSubmitButton();
});


// --------------------------------------------------
// SUBMIT RESPONSE
// --------------------------------------------------

elements.submitResponseButton.addEventListener("click", async () => {
    if (state.busy) return;

    stopAutoMonitor();

    const transcript = elements.transcriptText.value.trim();

    if (!transcript) {
        showInterviewError("Please provide a response before submitting.");
        return;
    }

    if (state.isRecording || state.voiceFinalizing) {
        showInterviewError(
            "Please finish voice recording before submitting a response."
        );
        return;
    }

    markResponseTurnStarted("typed");

    setBusy(true);
    elements.interviewError.textContent = "";
    elements.errorActions.classList.add("hidden");
    elements.interviewStatus.textContent = "PROCESSING";
    elements.voiceStatus.textContent = "Processing your response...";

    try {
        const payload = {
            transcript,
            speech_duration_ms: state.speechDurationMs,
            audio_issue: state.audioIssue
        };

        await apiRequest(
            `/sessions/${state.sessionId}/responses`,
            {
                method: "POST",
                body: JSON.stringify(payload)
            }
        );

        addActivity("RESPONSE", transcript, "response");

        elements.transcriptText.value = "";
        elements.transcriptStatus.textContent = "Response submitted";

        const result = await waitForResponseResult();

        await handleInterviewAction(result);

    } catch (error) {
        showInterviewError(error.message, {
            retryable: error.retryable === true
        });
    } finally {
        setBusy(false);
        updateSubmitButton();
    }
});


// --------------------------------------------------
// RECOVERY ACTIONS
// --------------------------------------------------

const RECOVERY_TITLES = {
    repeat_question: "LET'S REPEAT THAT",
    check_audibility: "QUICK CHECK",
    clarify_response: "A LITTLE MORE DETAIL"
};


function showRecoveryNotice(action, message) {
    elements.recoveryNotice.classList.remove("hidden");
    elements.recoveryTitle.textContent =
        RECOVERY_TITLES[action] || "LET'S TRY AGAIN";
    elements.recoveryMessage.textContent = message;
}


function hideRecoveryNotice() {
    elements.recoveryNotice.classList.add("hidden");
}


async function handleRecovery(payload) {
    elements.interviewStatus.textContent = "RECOVERY";

    const action = payload.action;
    const question = payload.question || state.currentQuestion;

    state.currentQuestion = question;
    elements.questionText.textContent = question;

    const recoveryMessages = {
        repeat_question: "Let's repeat the question.",
        check_audibility: "Could you hear the question clearly?",
        clarify_response: "Could you explain your answer a little more?",
        ask_repeat: "I missed that. Could you say it again?",
        redirect_to_question: "Let's come back to the question.",
        check_connection: "I'm having trouble hearing you. Please try again."
    };

    const message =
        recoveryMessages[action] ||
        "Let's try that question again.";

    showRecoveryNotice(action, message);
    elements.voiceStatus.textContent = message;
    enqueueVoiceFeedback(message, "recovery");

    if (action === "clarify_response") {
        elements.interviewStatus.textContent = "LISTENING";
        elements.voiceHint.textContent =
            "Please provide a clearer or more detailed response.";
        elements.voiceStatus.textContent =
            "Waiting for a clearer response.";
        return;
    }

    // The API allows advance for response_recovery.
    try {
        const result = await apiRequest(
            `/sessions/${state.sessionId}/advance`,
            { method: "POST" }
        );

        if (
            result.status === "waiting" &&
            result.action?.type === "await_consumer_response"
        ) {
            enterListeningState(
                result,
                "Please answer the same question again."
            );
        } else {
            await handleInterviewAction(result);
        }

    } catch (error) {
        showInterviewError(error.message);
    }
}


// --------------------------------------------------
// INTERVIEW COMPLETION
// --------------------------------------------------

function finishInterview() {
    window.speechSynthesis?.cancel();
    stopAutoMonitor();
    hideRecoveryNotice();
    hideFeedback();
    addActivity("COMPLETED", "The interview is complete.", "response");

    elements.interviewStatus.textContent = "COMPLETED";
    elements.questionText.textContent =
        "Thank you for sharing your experience.";

    elements.voiceStatus.textContent =
        "The interview has been completed.";

    elements.voiceHint.textContent =
        "Your responses have been processed.";

    elements.voiceVisual.classList.remove("is-recording");
    elements.recordButton.disabled = true;
    elements.recordButton.textContent = "Interview completed";

    elements.transcriptText.disabled = true;
    elements.submitResponseButton.disabled = true;

    speakText("Thank you for sharing your experience. The interview is complete.");
}


// --------------------------------------------------
// UI HELPERS
// --------------------------------------------------

function showQuestionLoading() {
    elements.questionLoading.classList.remove("hidden");
}

function hideQuestionLoading() {
    elements.questionLoading.classList.add("hidden");
}

function consentGiven() {
    return Boolean(elements.consentCheckbox?.checked);
}

function updateBeginButton() {
    elements.beginInterviewButton.disabled = state.busy ||
        !state.sessionId ||
        !consentGiven();
}

function setBusy(isBusy) {
    state.busy = isBusy;

    elements.permissionButton.disabled = isBusy ||
        !state.sessionId;
    updateBeginButton();
    elements.submitResponseButton.disabled = isBusy ||
        !elements.transcriptText.value.trim();

    elements.recordButton.disabled = isBusy ||
        !state.voiceSupported ||
        state.voiceFinalizing;

    if (
        !isBusy &&
        !state.questionPlaybackActive &&
        !state.isRecording &&
        !state.autoVadArmed &&
        elements.interviewStatus.textContent === "LISTENING"
    ) {
        armVoiceAnswerDetection();
    }
}

function showInterviewError(message, options = {}) {
    const detail = (message || "").trim();
    const retryable = options.retryable === true;

    hideQuestionLoading();
    elements.interviewError.textContent =
        detail
            ? `I couldn't finish that step: ${detail}`
            : "Something went wrong. Please try again.";
    elements.voiceStatus.textContent = retryable
        ? "Your answer was saved. Retry processing to continue."
        : detail
            ? `Couldn't finish that step: ${detail}`
            : "I couldn't finish that step. Please try again.";

    elements.errorActions.classList.toggle("hidden", !retryable);
    elements.retryProcessingButton.disabled = false;
}


async function retryResponseProcessing() {
    if (state.busy) return;

    setBusy(true);
    elements.interviewError.textContent = "";
    elements.errorActions.classList.add("hidden");
    elements.interviewStatus.textContent = "PROCESSING";
    elements.voiceStatus.textContent = "Retrying your saved response...";

    let scheduled = false;

    try {
        await apiRequest(
            `/sessions/${encodeURIComponent(state.sessionId)}` +
            `/responses/retry`,
            { method: "POST" }
        );
        scheduled = true;

        const result = await waitForResponseResult();
        await handleInterviewAction(result);

    } catch (error) {
        showInterviewError(error.message, {
            retryable: scheduled && error.retryable === true
        });

    } finally {
        setBusy(false);
        updateSubmitButton();
    }
}

elements.retryProcessingButton.addEventListener(
    "click",
    retryResponseProcessing
);


// --------------------------------------------------
// APPLICATION INITIALIZATION
// --------------------------------------------------

initializeVoiceSupport();
checkBackend();
loadInvitation();
