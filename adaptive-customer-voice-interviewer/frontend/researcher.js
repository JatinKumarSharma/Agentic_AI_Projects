const researcherElements = {
    connectionText: document.getElementById("researcherConnectionText"),
    refreshButton: document.getElementById("refreshButton"),
    sessionListPanel: document.getElementById("sessionListPanel"),
    sessionList: document.getElementById("sessionList"),
    sessionCount: document.getElementById("sessionCount"),
    sessionDetailPanel: document.getElementById("sessionDetailPanel"),
    backButton: document.getElementById("backButton"),
    detailTitle: document.getElementById("detailTitle"),
    detailSubtitle: document.getElementById("detailSubtitle"),
    detailActivity: document.getElementById("detailActivity"),
    metricGrid: document.getElementById("metricGrid"),
    conversationTimeline: document.getElementById("conversationTimeline"),
    timingList: document.getElementById("timingList"),
    knowledgeList: document.getElementById("knowledgeList"),
    gapList: document.getElementById("gapList"),
    insightBlock: document.getElementById("insightBlock"),
    qualityList: document.getElementById("qualityList"),
    requirementList: document.getElementById("requirementList"),
    insightOutput: document.getElementById("insightOutput"),
    reportFilterForm: document.getElementById("reportFilterForm"),
    reportPeriodStart: document.getElementById("reportPeriodStart"),
    reportPeriodEnd: document.getElementById("reportPeriodEnd"),
    reportSegment: document.getElementById("reportSegment"),
    reportCategory: document.getElementById("reportCategory"),
    reportGoal: document.getElementById("reportGoal"),
    reportStatus: document.getElementById("reportStatus"),
    reportPattern: document.getElementById("reportPattern"),
    reportCount: document.getElementById("reportCount"),
    reportAggregate: document.getElementById("reportAggregate"),
    reportList: document.getElementById("reportList"),
    reportLedger: document.getElementById("reportLedger"),
    exportReportsLink: document.getElementById("exportReportsLink"),
    sessionReportSummary: document.getElementById("sessionReportSummary"),

    tabs: document.querySelectorAll(".researcher-tab"),
    createPanel: document.getElementById("createPanel"),
    sessionsView: document.getElementById("sessionsView"),
    analysisView: document.getElementById("analysisView"),
    createForm: document.getElementById("createSessionForm"),
    createError: document.getElementById("createError"),
    createResult: document.getElementById("createResult"),
    createResultTitle: document.getElementById("createResultTitle"),
    createResultFacts: document.getElementById("createResultFacts"),
    participantLink: document.getElementById("participantLink"),
    copyLinkButton: document.getElementById("copyLinkButton"),
    viewCreatedSessionButton: document.getElementById(
        "viewCreatedSessionButton"
    ),
    resetCreateButton: document.getElementById("resetCreateButton"),
    sessionFacts: document.getElementById("sessionFacts"),
    groupBy: document.getElementById("sessionGroupBy"),
    sortBy: document.getElementById("sessionSortBy"),
    sortOrder: document.getElementById("sessionSortOrder"),
    sessionSearch: document.getElementById("sessionSearch"),
    linkExpiry: document.getElementById("linkExpiry"),
    customExpiryFields: document.getElementById("customExpiryFields"),
    expiryDateTime: document.getElementById("expiryDateTime"),
    expiryTimezone: document.getElementById("expiryTimezone"),
    expiryCustomOffset: document.getElementById("expiryCustomOffset"),
    customOffsetField: document.getElementById("customOffsetField"),
};

let selectedSessionId = null;
let activeView = "create";
let lastCreatedSessionId = null;
let cachedSessions = [];


async function researcherApiRequest(path, options = {}) {
    const response = await fetch(path, {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {})
        }
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(
            data.detail || `Request failed with status ${response.status}`
        );
    }

    return data;
}


function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}


function humanize(value) {
    return String(value ?? "—")
        .replaceAll("_", " ")
        .replace(/\b\w/g, (character) => character.toUpperCase());
}


function emptyState(message) {
    return `<p class="empty-state">${escapeHtml(message)}</p>`;
}


function activityClass(activity) {
    if (activity === "completed") return "is-complete";
    if (activity === "failed") return "is-failed";
    if (activity && activity.startsWith("awaiting")) return "is-waiting";
    if (activity === "processing_response") return "is-live";
    return "";
}


function sessionGroupKey(session) {
    const groupBy = researcherElements.groupBy.value;

    if (groupBy === "company") {
        return session.company_name || "Unknown company";
    }

    if (groupBy === "category") {
        return session.product_category || "Uncategorized";
    }

    if (groupBy === "none") {
        return "All research sessions";
    }

    return `${session.company_name || "Unknown company"} · ${
        session.product_name || "Unknown product"
    }`;
}


function compareSessions(left, right) {
    const key = researcherElements.sortBy.value;
    const direction = researcherElements.sortOrder.value === "asc" ? 1 : -1;

    const numericKeys = new Set([
        "questions_asked_count",
        "evidence_count",
        "iteration_count",
        "responses_count",
    ]);

    let result;

    if (numericKeys.has(key)) {
        result = Number(left[key] || 0) - Number(right[key] || 0);
    } else {
        const leftValue = left[key] || left.created_at || left.start_date || "";
        const rightValue = right[key] || right.created_at || right.start_date || "";
        result = String(leftValue).localeCompare(String(rightValue));
    }

    if (result === 0) {
        result = String(left.created_at || "").localeCompare(
            String(right.created_at || "")
        );
    }

    return result * direction;
}


function renderSessions(sessions) {
    researcherElements.sessionCount.textContent =
        `${sessions.length} session${sessions.length === 1 ? "" : "s"}`;

    if (!sessions.length) {
        researcherElements.sessionList.innerHTML = emptyState(
            "No research sessions yet. Create one from the Create interview tab."
        );
        return;
    }

    const query = researcherElements.sessionSearch.value.trim().toLowerCase();

    const matching = sessions
        .filter((session) => {
            if (!query) return true;

            return [
                session.company_name,
                session.product_name,
                session.product_category,
                session.research_goal,
                session.customer_segment,
            ].some((value) => String(value || "").toLowerCase().includes(query));
        })
        .sort(compareSessions);

    if (!matching.length) {
        researcherElements.sessionList.innerHTML = emptyState(
            "No sessions match this search."
        );
        return;
    }

    const groups = new Map();

    matching.forEach((session) => {
        const key = sessionGroupKey(session);

        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(session);
    });

    researcherElements.sessionList.innerHTML = [...groups.entries()].map(
        ([groupName, groupSessions]) => `
            <section class="session-group">
                <div class="group-heading">
                    <div>
                        <p class="eyebrow">SESSION GROUP</p>
                        <h3>${escapeHtml(groupName)}</h3>
                    </div>
                    <span class="step-label">
                        ${groupSessions.length} session${
                            groupSessions.length === 1 ? "" : "s"
                        }
                    </span>
                </div>
                <div class="session-list">
                    ${groupSessions.map((session) => `
                        <button type="button" class="session-card"
                                data-session-id="${escapeHtml(session.session_id)}">
                            <div class="session-card-top">
                                <span class="activity-pill ${
                                    activityClass(session.activity)
                                }">
                                    ${escapeHtml(humanize(session.activity))}
                                </span>
                                <span class="session-iteration">
                                    Iteration ${escapeHtml(session.iteration_count)}
                                </span>
                            </div>
                            <h3>${escapeHtml(session.company_name || "Unknown company")}
                                · ${escapeHtml(session.product_name || "Unknown product")}</h3>
                            <p class="session-goal">${escapeHtml(
                                session.research_goal || "No research goal"
                            )}</p>
                            <div class="session-stats">
                                <span>${escapeHtml(session.questions_asked_count)} questions</span>
                                <span>${escapeHtml(session.evidence_count)} evidence</span>
                                <span>${escapeHtml(session.unresolved_gaps_count)} gaps</span>
                                ${session.customer_segment
                                    ? `<span>${escapeHtml(session.customer_segment)}</span>`
                                    : ""}
                                ${session.link_expires_at
                                    ? `<span>Link → ${escapeHtml(session.link_expires_at)}</span>`
                                    : ""}
                            </div>
                        </button>
                    `).join("")}
                </div>
            </section>
        `
    ).join("");

    researcherElements.sessionList.querySelectorAll(".session-card").forEach(
        (card) => {
            card.addEventListener("click", () => {
                loadSessionDetail(card.dataset.sessionId);
            });
        }
    );
}


function renderMetrics(detail) {
    const evidenceCount = detail.knowledge.reduce(
        (total, item) => total + (item.values || []).length,
        0,
    );

    const metrics = [
        ["Iteration", detail.iteration_count ?? 0],
        ["Questions", detail.conversation.length],
        ["Responses", detail.conversation.filter((turn) => turn.response).length],
        ["Evidence", evidenceCount],
        ["Open gaps", detail.gaps.length],
        ["State snapshots", detail.state_history_count],
    ];

    researcherElements.metricGrid.innerHTML = metrics.map(([label, value]) => `
        <div class="metric-card">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(value)}</strong>
        </div>
    `).join("");
}


function renderConversation(timeline) {
    if (!timeline.length) {
        researcherElements.conversationTimeline.innerHTML =
            emptyState("No interview turns have been recorded yet.");
        return;
    }

    researcherElements.conversationTimeline.innerHTML = timeline.map((turn) => `
        <div class="timeline-item">
            <div class="timeline-marker">${escapeHtml(turn.turn)}</div>
            <div class="timeline-content">
                <div class="timeline-turn">
                    ${turn.question
                        ? escapeHtml(turn.question)
                        : "<em>No question recorded</em>"}
                </div>
                <div class="timeline-response">
                    ${turn.response
                        ? escapeHtml(turn.response)
                        : "<em>Awaiting response</em>"}
                </div>
            </div>
        </div>
    `).join("");
}


function renderTimings(timings) {
    if (!timings.length) {
        researcherElements.timingList.innerHTML =
            emptyState("No latency spans have been recorded yet.");
        return;
    }

    researcherElements.timingList.innerHTML = timings.map((item) => {
        const status = item.metadata?.status
            ? ` · ${escapeHtml(humanize(item.metadata.status))}`
            : "";

        return `
            <div class="timing-item">
                <div class="timing-marker">T${escapeHtml(item.turn)}</div>
                <div class="timing-content">
                    <div class="timing-turn">
                        ${escapeHtml(humanize(item.span_name))}
                        <span class="timing-source">${escapeHtml(item.source)}</span>
                    </div>
                    <div class="timing-detail">
                        ${Number(item.duration_ms).toFixed(1)} ms${status}
                    </div>
                </div>
            </div>
        `;
    }).join("");
}


function renderKnowledge(items) {
    if (!items.length) {
        researcherElements.knowledgeList.innerHTML =
            emptyState("No structured evidence has been accumulated yet.");
        return;
    }

    researcherElements.knowledgeList.innerHTML = items.map((item) => {
        const values = item.values || [];

        const badges = [
            item.sufficiency,
            item.confidence,
            item.verification_status,
            item.criticality,
        ].filter(Boolean).map((badge) => `
            <span class="badge">${escapeHtml(humanize(badge))}</span>
        `).join("");

        return `
            <article class="knowledge-item">
                <div class="knowledge-title">
                    <strong>${escapeHtml(humanize(item.category))}</strong>
                    <span>${escapeHtml(humanize(item.parameter))}</span>
                </div>
                <div class="badge-row">${badges}</div>
                <ul class="evidence-list">
                    ${values.map((evidence) => `
                        <li>
                            <span>${escapeHtml(evidence.value)}</span>
                            <small>${escapeHtml(humanize(evidence.sentiment))}
                                · ${escapeHtml(humanize(evidence.confidence))}
                                · ${escapeHtml(humanize(evidence.evidence_type))}
                            </small>
                        </li>
                    `).join("")}
                </ul>
            </article>
        `;
    }).join("");
}


function renderGaps(gaps) {
    if (!gaps.length) {
        researcherElements.gapList.innerHTML =
            emptyState("No unresolved information gaps.");
        return;
    }

    researcherElements.gapList.innerHTML = gaps.map((gap) => `
        <article class="gap-item">
            <div class="gap-title">
                <strong>${escapeHtml(gap.parameter || "Unnamed gap")}</strong>
                <span class="badge">${escapeHtml(humanize(gap.gap_type))}</span>
            </div>
            <p>${escapeHtml(humanize(gap.category))}</p>
            <div class="badge-row">
                <span class="badge">Importance: ${escapeHtml(humanize(gap.importance))}</span>
                <span class="badge">Criticality: ${escapeHtml(humanize(gap.criticality))}</span>
                <span class="badge">Alignment: ${escapeHtml(humanize(gap.goal_alignment))}</span>
            </div>
        </article>
    `).join("");
}


function renderInsights(detail) {
    const proposal = detail.selected_proposal;
    const reflection = detail.reflection;
    const blocks = [];

    if (proposal) {
        blocks.push(`
            <div class="insight-item">
                <p class="eyebrow">NEXT QUESTION PROPOSAL</p>
                <h4>${escapeHtml(proposal.question || "No question text")}</h4>
                <p>${escapeHtml(proposal.target_gap || "No target gap")}</p>
            </div>
        `);
    }

    if (reflection) {
        blocks.push(`
            <div class="insight-item">
                <p class="eyebrow">LATEST REFLECTION</p>
                <dl class="reflection-list">
                    ${Object.entries(reflection).map(([key, value]) => `
                        <div>
                            <dt>${escapeHtml(humanize(key))}</dt>
                            <dd>${escapeHtml(humanize(value))}</dd>
                        </div>
                    `).join("")}
                </dl>
            </div>
        `);
    }

    researcherElements.insightBlock.innerHTML = blocks.length
        ? blocks.join("")
        : emptyState("No decision trail available yet.");
}


function renderQuality(quality) {
    const groups = Object.entries(quality || {}).filter(
        ([, values]) => (
            values
            && typeof values === "object"
            && Object.keys(values).length > 0
        ),
    );

    if (!groups.length) {
        researcherElements.qualityList.innerHTML =
            emptyState("No quality indicators available yet.");
        return;
    }

    researcherElements.qualityList.innerHTML = groups.map(([group, values]) => `
        <div class="quality-group">
            <h4>${escapeHtml(humanize(group))}</h4>
            <div class="badge-row">
                ${Object.entries(values).map(([key, value]) => `
                    <span class="badge">${escapeHtml(key)}: ${escapeHtml(humanize(value))}</span>
                `).join("")}
            </div>
        </div>
    `).join("");
}


function renderRequirements(metaPlan) {
    const requirements = metaPlan?.required_information || [];

    if (!requirements.length) {
        researcherElements.requirementList.innerHTML =
            emptyState("No meta-plan requirements available yet.");
        return;
    }

    researcherElements.requirementList.innerHTML = requirements.map((requirement) => `
        <article class="requirement-item">
            <strong>${escapeHtml(requirement.parameter || "Unnamed requirement")}</strong>
            <div class="badge-row">
                <span class="badge">Importance: ${escapeHtml(humanize(requirement.importance))}</span>
                <span class="badge">Alignment: ${escapeHtml(humanize(requirement.goal_alignment))}</span>
                <span class="badge">Criticality: ${escapeHtml(humanize(requirement.criticality))}</span>
            </div>
        </article>
    `).join("");
}


function renderFinalInsights(finalInsights) {
    if (!finalInsights || !Object.keys(finalInsights).length) {
        researcherElements.insightOutput.innerHTML =
            emptyState("Final insights appear when the interview completes.");
        return;
    }

    researcherElements.insightOutput.innerHTML =
        `<pre class="insight-json">${escapeHtml(JSON.stringify(finalInsights, null, 2))}</pre>`;
}


function renderSessionFacts(session) {
    const facts = [
        ["Session ID", session.session_id],
        ["Company", session.company_name],
        ["Company ID", session.company_id],
        ["Product", session.product_name],
        ["Product ID", session.product_id],
        ["Product category", session.product_category],
        ["Research goal", session.research_goal],
        ["Customer segment", session.customer_segment],
        ["Target audience", session.target_audience],
        ["Participant ID", session.participant_id],
        ["Start date", session.start_date],
        ["End date", session.end_date],
        ["Link expiry", session.link_expiry],
        ["Link expires at (UTC)", session.link_expires_at],
        ["Interview started at", session.interview_started_at],
        ["Lifecycle status", session.status],
        ["Constraints", (session.interview_constraints || []).join(", ")],
    ];

    researcherElements.sessionFacts.innerHTML = facts.map(([label, value]) => `
        <div class="fact-item">
            <dt>${escapeHtml(label)}</dt>
            <dd>${escapeHtml(value || "—")}</dd>
        </div>
    `).join("");
}


function renderDetail(detail) {
    const session = detail.session || {};

    researcherElements.detailTitle.textContent =
        `${session.product_name || "Product"} · ${session.company_name || "Company"}`;

    researcherElements.detailSubtitle.textContent =
        session.research_goal || "No research goal provided.";

    researcherElements.detailActivity.textContent =
        humanize(session.activity);
    researcherElements.detailActivity.className =
        `activity-pill ${activityClass(session.activity)}`;

    renderSessionFacts(session);
    renderMetrics(detail);
    renderConversation(detail.conversation || []);
    renderTimings(detail.timings || []);
    renderKnowledge(detail.knowledge || []);
    renderGaps(detail.gaps || []);
    renderInsights(detail);
    renderQuality(detail.quality);
    renderRequirements(detail.meta_plan);
    renderFinalInsights(detail.final_insights);

    // Detail is a sub-view of Research sessions. Hide every other workspace
    // so filters from the Analysis view never appear above a session.
    showView("sessions");
    researcherElements.sessionListPanel.classList.add("hidden");
    researcherElements.sessionDetailPanel.classList.remove("hidden");

    window.scrollTo({ top: 0, behavior: "smooth" });
}


function reportQueryParams() {
    const params = new URLSearchParams();

    const values = [
        ["period_start", researcherElements.reportPeriodStart.value],
        ["period_end", researcherElements.reportPeriodEnd.value],
        ["customer_segment", researcherElements.reportSegment.value.trim()],
        ["product_category", researcherElements.reportCategory.value.trim()],
        ["research_goal", researcherElements.reportGoal.value.trim()],
        ["status_label", researcherElements.reportStatus.value],
        [
            "response_pattern_label",
            researcherElements.reportPattern.value,
        ],
    ];

    values.forEach(([key, value]) => {
        if (value) params.set(key, value);
    });

    return params;
}


function renderReportAggregate(aggregate) {
    const metrics = [
        ["Reports", aggregate.report_count ?? 0],
        ["Claims", aggregate.claim_count ?? 0],
        ["Evidence refs", aggregate.evidence_count ?? 0],
        [
            "Segments",
            (aggregate.by_customer_segment || []).length,
        ],
        [
            "Categories",
            (aggregate.by_product_category || []).length,
        ],
        [
            "Periods",
            (aggregate.by_period || []).length,
        ],
        [
            "Unfinished",
            (aggregate.by_status_label || []).find(
                (item) => item.value === "unfinished",
            )?.report_count ?? 0,
        ],
        [
            "Recovered",
            (aggregate.by_response_pattern_label || []).find(
                (item) => item.value === "recovered_after_invalid",
            )?.report_count ?? 0,
        ],
    ];

    researcherElements.reportAggregate.innerHTML = metrics.map(
        ([label, value]) => `
            <div class="metric-card">
                <span>${escapeHtml(label)}</span>
                <strong>${escapeHtml(value)}</strong>
            </div>
        `,
    ).join("");
}


function renderReports(reports) {
    researcherElements.reportCount.textContent =
        `${reports.length} report${reports.length === 1 ? "" : "s"}`;

    if (!reports.length) {
        researcherElements.reportList.innerHTML = emptyState(
            "No grounded reports match these filters yet.",
        );
        return;
    }

    researcherElements.reportList.innerHTML = reports.map((report) => {
        const summary = report.summary || {};
        const dimensions = report.dimensions || {};
        const statusLabel = dimensions.status_label || "unclassified";
        const patternLabel = (
            dimensions.response_pattern_label || "unclassified"
        );
        const statusClass = statusLabel === "unfinished"
            ? "is-waiting"
            : "is-complete";
        const patternClass = patternLabel === "recovered_after_invalid"
            ? "is-live"
            : (patternLabel === "unresolved_invalid" ? "is-failed" : "");

        return `
            <button type="button" class="session-card report-card"
                    data-report-id="${escapeHtml(report.report_id)}">
                <div class="session-card-top">
                    <span class="activity-pill ${statusClass}">
                        ${escapeHtml(humanize(statusLabel))}
                    </span>
                    <span class="activity-pill ${patternClass}">
                        ${escapeHtml(humanize(patternLabel))}
                    </span>
                    <span class="session-iteration">
                        ${escapeHtml(summary.claim_count ?? 0)} claims
                    </span>
                </div>
                <h3>${escapeHtml(dimensions.product_name || "Product")}
                    · ${escapeHtml(dimensions.company_name || "Company")}</h3>
                <p class="session-goal">
                    ${escapeHtml(dimensions.research_goal || "No research goal")}
                </p>
                <div class="session-stats">
                    <span>${escapeHtml(dimensions.customer_segment)}</span>
                    <span>${escapeHtml(dimensions.product_category)}</span>
                    <span>${escapeHtml(
                        dimensions.period_start || "No period"
                    )}</span>
                    <span>${escapeHtml(summary.evidence_count ?? 0)} evidence</span>
                </div>
            </button>
        `;
    }).join("");

    researcherElements.reportList.querySelectorAll(".report-card").forEach(
        (card) => {
            card.addEventListener("click", () => {
                loadReportDetail(card.dataset.reportId);
            });
        },
    );
}


function renderClaimLedger(report) {
    const claims = report.claims || [];

    if (!claims.length) {
        researcherElements.reportLedger.innerHTML = emptyState(
            "This report has no grounded claims.",
        );
        return;
    }

    researcherElements.reportLedger.innerHTML = `
        <div class="card-heading">
            <p class="eyebrow">CLAIM LEDGER</p>
            <h3>${escapeHtml(report.report_id)}</h3>
        </div>
        ${claims.map((claim) => `
            <article class="claim-item">
                <div class="knowledge-title">
                    <strong>${escapeHtml(humanize(claim.category))}</strong>
                    <span>${escapeHtml(humanize(claim.parameter))}</span>
                </div>
                <p>${escapeHtml(claim.statement)}</p>
                <ul class="evidence-list">
                    ${(claim.evidence || []).map((evidence) => `
                        <li>
                            <span>“${escapeHtml(evidence.source_quote)}”</span>
                            <small>
                                ${escapeHtml(evidence.response_id)}
                                · turn ${escapeHtml(evidence.turn)}
                                · ${escapeHtml(humanize(evidence.confidence))}
                            </small>
                        </li>
                    `).join("")}
                </ul>
            </article>
        `).join("")}
    `;
}


function renderSessionReport(reports) {
    if (!reports.length) {
        researcherElements.sessionReportSummary.innerHTML = emptyState(
            "No analysis report has been generated for this session yet.",
        );
        return;
    }

    const report = reports[0];
    const summary = report.summary || {};

    researcherElements.sessionReportSummary.innerHTML = `
        <div class="insight-item">
            <p class="eyebrow">VALIDATION</p>
            <h4>${report.validation?.valid ? "Fully grounded" : "Rejected"}</h4>
            <p>
                ${escapeHtml(summary.claim_count ?? 0)} claims ·
                ${escapeHtml(summary.evidence_count ?? 0)} evidence references ·
                ${escapeHtml(summary.response_count ?? 0)} responses
            </p>
        </div>
        <div class="insight-item">
            <p class="eyebrow">INTERVIEW LABELS</p>
            <h4>
                ${escapeHtml(humanize(
                    report.dimensions?.status_label || "unclassified"
                ))}
                ·
                ${escapeHtml(humanize(
                    report.dimensions?.response_pattern_label
                    || "unclassified"
                ))}
            </h4>
            <p>
                ${escapeHtml(summary.valid_response_count ?? 0)} valid ·
                ${escapeHtml(summary.invalid_response_count ?? 0)} invalid
                responses
            </p>
        </div>
        <div class="insight-item">
            <p class="eyebrow">SEGMENT &amp; PERIOD</p>
            <h4>${escapeHtml(report.dimensions?.customer_segment)}</h4>
            <p>
                ${escapeHtml(report.dimensions?.period_start || "—")}
                → ${escapeHtml(report.dimensions?.period_end || "—")}
            </p>
        </div>
        <div class="insight-item">
            <p class="eyebrow">SENTIMENT</p>
            <dl class="reflection-list">
                ${Object.entries(summary.sentiment_distribution || {}).map(
                    ([key, value]) => `
                        <div>
                            <dt>${escapeHtml(humanize(key))}</dt>
                            <dd>${escapeHtml(value)}</dd>
                        </div>
                    `,
                ).join("")}
            </dl>
        </div>
    `;
}


async function loadReportDetail(reportId) {
    try {
        const report = await researcherApiRequest(
            `/researcher/analysis/reports/${encodeURIComponent(reportId)}`,
        );

        renderClaimLedger(report);
    } catch (error) {
        console.error(error);
        researcherElements.reportLedger.innerHTML = emptyState(
            "Could not load this report ledger.",
        );
    }
}


async function loadSessionReport(sessionId) {
    try {
        const params = new URLSearchParams({ session_id: sessionId });
        const result = await researcherApiRequest(
            `/researcher/analysis/reports?${params}`,
        );

        renderSessionReport(result.reports || []);
    } catch (error) {
        console.error(error);
        researcherElements.sessionReportSummary.innerHTML = emptyState(
            "Could not load the grounded analysis report.",
        );
    }
}


async function loadReports() {
    try {
        const params = reportQueryParams();
        const result = await researcherApiRequest(
            `/researcher/analysis/reports?${params}`,
        );

        renderReportAggregate(result.aggregate || {});
        renderReports(result.reports || []);
        researcherElements.exportReportsLink.setAttribute(
            "href",
            `/researcher/analysis/reports/export.csv?${params}`,
        );
    } catch (error) {
        console.error(error);
        researcherElements.reportList.innerHTML = emptyState(
            "Could not load analysis reports.",
        );
    }
}


async function loadSessions() {
    try {
        const result = await researcherApiRequest("/researcher/sessions");
        cachedSessions = result.sessions || [];
        renderSessions(cachedSessions);
        await loadReports();
        researcherElements.connectionText.textContent = "Connected to backend";
    } catch (error) {
        console.error(error);
        researcherElements.connectionText.textContent = "Backend unavailable";
        researcherElements.sessionList.innerHTML =
            emptyState("Could not load research sessions.");
    }
}


async function loadSessionDetail(sessionId) {
    try {
        const detail = await researcherApiRequest(
            `/researcher/sessions/${encodeURIComponent(sessionId)}`,
        );

        selectedSessionId = sessionId;
        renderDetail(detail);
        await loadSessionReport(sessionId);
    } catch (error) {
        console.error(error);
        window.alert("Could not load this research session.");
    }
}


function showView(view) {
    activeView = view;

    researcherElements.tabs.forEach((tab) => {
        const selected = tab.dataset.view === view;
        tab.classList.toggle("active", selected);
        tab.setAttribute("aria-selected", String(selected));
    });

    researcherElements.createPanel.classList.toggle(
        "hidden", view !== "create"
    );
    researcherElements.sessionsView.classList.toggle(
        "hidden", view !== "sessions"
    );
    researcherElements.analysisView.classList.toggle(
        "hidden", view !== "analysis"
    );

    if (view !== "sessions") {
        researcherElements.sessionListPanel.classList.remove("hidden");
        researcherElements.sessionDetailPanel.classList.add("hidden");
        selectedSessionId = null;
    }
}


function toggleCustomExpiryFields() {
    researcherElements.customExpiryFields.classList.toggle(
        "hidden", researcherElements.linkExpiry.value !== "custom"
    );
}


function toggleCustomOffsetField() {
    researcherElements.customOffsetField.classList.toggle(
        "hidden", researcherElements.expiryTimezone.value !== "custom"
    );
}


function formValue(id) {
    return document.getElementById(id).value.trim();
}


function optionalFormValue(id) {
    return formValue(id) || null;
}


function buildLinkExpiry() {
    const preset = researcherElements.linkExpiry.value;

    if (preset !== "custom") return preset;

    const dateTime = researcherElements.expiryDateTime.value;

    if (!dateTime) {
        throw new Error("Choose a custom expiry date and time.");
    }

    let offset = researcherElements.expiryTimezone.value;

    if (offset === "custom") {
        offset = researcherElements.expiryCustomOffset.value.trim();

        if (!/^[+-]\d{2}:\d{2}$/.test(offset)) {
            throw new Error(
                "Enter a timezone offset such as +05:30 or -08:00."
            );
        }
    }

    const normalized = dateTime.length === 16
        ? `${dateTime}:00`
        : dateTime;

    return `${normalized}${offset}`;
}


researcherElements.createForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    researcherElements.createError.textContent = "";

    const submitButton = document.getElementById("createSessionButton");
    submitButton.disabled = true;

    try {
        const constraints = formValue("interviewConstraints")
            .split(/[,\n]/)
            .map((item) => item.trim())
            .filter(Boolean);

        const result = await researcherApiRequest("/sessions", {
            method: "POST",
            body: JSON.stringify({
                company_id: formValue("companyId"),
                company_name: formValue("companyName"),
                product_id: formValue("productId"),
                product_name: formValue("productName"),
                product_category: formValue("productCategory"),
                research_goal: formValue("researchGoal"),
                customer_segment: optionalFormValue("customerSegment"),
                target_audience: optionalFormValue("targetAudience"),
                start_date: formValue("sessionStartDate")
                    || new Date().toISOString().slice(0, 10),
                end_date: optionalFormValue("sessionEndDate"),
                participant_id: optionalFormValue("participantId"),
                interview_constraints: constraints,
                link_expiry: buildLinkExpiry()
            })
        });

        lastCreatedSessionId = result.session_id;
        researcherElements.participantLink.value =
            `${window.location.origin}${result.participant_url}`;

        researcherElements.createResultTitle.textContent =
            `${result.product_name || "Interview"} · link generated`;

        researcherElements.createResultFacts.innerHTML = `
            <div class="fact-item">
                <dt>Session ID</dt>
                <dd>${escapeHtml(result.session_id)}</dd>
            </div>
            <div class="fact-item">
                <dt>Company</dt>
                <dd>${escapeHtml(result.company_name)}</dd>
            </div>
            <div class="fact-item">
                <dt>Research goal</dt>
                <dd>${escapeHtml(result.research_goal)}</dd>
            </div>
            <div class="fact-item">
                <dt>Link expires at (UTC)</dt>
                <dd>${escapeHtml(result.link_expires_at || "Never")}</dd>
            </div>
        `;

        researcherElements.createResult.classList.remove("hidden");
        await loadSessions();
    } catch (error) {
        researcherElements.createError.textContent = error.message;
    } finally {
        submitButton.disabled = false;
    }
});


researcherElements.createForm.addEventListener("reset", () => {
    researcherElements.createResult.classList.add("hidden");
    researcherElements.createError.textContent = "";
    lastCreatedSessionId = null;
    window.setTimeout(() => {
        toggleCustomExpiryFields();
        toggleCustomOffsetField();
    }, 0);
});


researcherElements.linkExpiry.addEventListener(
    "change", toggleCustomExpiryFields
);
researcherElements.expiryTimezone.addEventListener(
    "change", toggleCustomOffsetField
);


researcherElements.copyLinkButton.addEventListener("click", async () => {
    const link = researcherElements.participantLink.value;

    if (!link) return;

    try {
        await navigator.clipboard.writeText(link);
    } catch {
        researcherElements.participantLink.select();
        document.execCommand("copy");
    }

    researcherElements.copyLinkButton.textContent = "Copied";

    window.setTimeout(() => {
        researcherElements.copyLinkButton.textContent = "Copy link";
    }, 1800);
});


researcherElements.viewCreatedSessionButton.addEventListener(
    "click", () => {
        if (!lastCreatedSessionId) return;
        showView("sessions");
        loadSessionDetail(lastCreatedSessionId);
    }
);


researcherElements.tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
        if (tab.dataset.view === "sessions") {
            researcherElements.sessionListPanel.classList.remove("hidden");
            researcherElements.sessionDetailPanel.classList.add("hidden");
            selectedSessionId = null;
        }

        showView(tab.dataset.view);
    });
});


researcherElements.groupBy.addEventListener("change", () => loadSessions());
researcherElements.sortBy.addEventListener("change", () => loadSessions());
researcherElements.sortOrder.addEventListener("change", () => loadSessions());
researcherElements.sessionSearch.addEventListener("input", () => {
    if (cachedSessions.length) renderSessions(cachedSessions);
});


researcherElements.reportFilterForm.addEventListener("submit", (event) => {
    event.preventDefault();
    loadReports();
});


researcherElements.refreshButton.addEventListener("click", () => {
    if (activeView === "analysis") {
        loadReports();
    } else if (activeView === "sessions") {
        if (selectedSessionId) {
            loadSessionDetail(selectedSessionId);
        } else {
            loadSessions();
        }
    } else {
        loadSessions();
    }
});


researcherElements.backButton.addEventListener("click", () => {
    selectedSessionId = null;
    researcherElements.sessionDetailPanel.classList.add("hidden");
    researcherElements.sessionListPanel.classList.remove("hidden");
    loadSessions();
});


toggleCustomExpiryFields();
toggleCustomOffsetField();
loadSessions();

window.setInterval(() => {
    if (activeView === "sessions" && !selectedSessionId) {
        loadSessions();
    }
}, 8000);
