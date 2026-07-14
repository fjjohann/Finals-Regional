const QUALIFIED_LIMIT = 12;
const SIMULATION_STORAGE_KEY = "finalsRegional.simulation.confirmations.v1";
const SIMULATION_RELEASE_STORAGE_KEY = "finalsRegional.simulation.releases.v1";
const SIMULATION_STATE_RELEASE_STORAGE_KEY = "finalsRegional.simulation.stateReleases.v1";
const ADMIN_SESSION_KEY = "finalsRegional.adminSession.v1";
const REMOTE_STATE_ID = "global";
const REMOTE_REFRESH_INTERVAL_MS = 8000;
const ADMIN_CONFIG = window.FINALS_ADMIN_CONFIG || {};
const SUPABASE_PUBLIC_KEY = ADMIN_CONFIG.supabaseAnonKey || ADMIN_CONFIG.supabasePublishableKey || "";
const hasRemoteAdminConfig = Boolean(ADMIN_CONFIG.supabaseUrl && SUPABASE_PUBLIC_KEY);

const state = {
  data: null,
  rankings: [],
  selectedCategory: "",
  activeView: "regionals",
  confirmations: {},
  releases: {},
  stateReleases: {},
  remoteConfirmations: {},
  remoteReleases: {},
  remoteStateReleases: {},
  remoteStateSignature: "",
  simulationConfirmations: loadConfirmations(),
  simulationReleases: loadReleases(),
  simulationStateReleases: loadStateReleases(),
  admin: {
    configured: hasRemoteAdminConfig,
    session: loadAdminSession(),
    saving: false,
  },
};

let persistTimer = null;
let remoteRefreshTimer = null;

const els = {
  updatedAt: document.querySelector("#updatedAt"),
  adminStatus: document.querySelector("#adminStatus"),
  adminToggle: document.querySelector("#adminToggle"),
  adminDialog: document.querySelector("#adminDialog"),
  adminLoginForm: document.querySelector("#adminLoginForm"),
  adminEmail: document.querySelector("#adminEmail"),
  adminPassword: document.querySelector("#adminPassword"),
  adminMessage: document.querySelector("#adminMessage"),
  adminCancel: document.querySelector("#adminCancel"),
  viewTabs: Array.from(document.querySelectorAll(".view-tab")),
  viewPanels: Array.from(document.querySelectorAll("[data-view-panel]")),
  regionalToolbar: document.querySelector("#regionalToolbar"),
  categoryFilter: document.querySelector("#categoryFilter"),
  selectedMeta: document.querySelector("#selectedMeta"),
  selectedTitle: document.querySelector("#selectedTitle"),
  regionalGrid: document.querySelector("#regionalGrid"),
  federationGrid: document.querySelector("#federationGrid"),
  finalsGrid: document.querySelector("#finalsGrid"),
  emptyState: document.querySelector("#emptyState"),
};

function uniqueBy(items, keyFn) {
  const map = new Map();
  items.forEach((item) => {
    const key = keyFn(item);
    if (!map.has(key)) map.set(key, item);
  });
  return Array.from(map.values());
}

function option(value, label) {
  const item = document.createElement("option");
  item.value = value;
  item.textContent = label;
  return item;
}

function categoryLabel(ranking) {
  return `${ranking.gender} ${ranking.categoryLabel} (${ranking.categoryCode})`;
}

function categoryKey(ranking) {
  return ranking.categoryKey;
}

function allCategories() {
  return uniqueBy(state.rankings, categoryKey)
    .sort((a, b) => {
      const groupOrder = ["Subs", "Idades", "Tecnicas"];
      const groupDiff = groupOrder.indexOf(a.categoryGroup) - groupOrder.indexOf(b.categoryGroup);
      if (groupDiff !== 0) return groupDiff;
      return categoryLabel(a).localeCompare(categoryLabel(b), "pt-BR");
    });
}

function rankingsForCategory(key) {
  return state.rankings
    .filter((item) => categoryKey(item) === key && item.rankingScope === "regional")
    .sort((a, b) => Number(a.regionalId) - Number(b.regionalId));
}

function stateRankingForCategory(key) {
  return state.rankings.find((item) => categoryKey(item) === key && item.rankingScope === "state");
}

function loadConfirmations() {
  try {
    return JSON.parse(localStorage.getItem(SIMULATION_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function loadReleases() {
  try {
    return JSON.parse(localStorage.getItem(SIMULATION_RELEASE_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function loadStateReleases() {
  try {
    return JSON.parse(localStorage.getItem(SIMULATION_STATE_RELEASE_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function loadAdminSession() {
  try {
    const session = JSON.parse(localStorage.getItem(ADMIN_SESSION_KEY) || "null");
    if (!session?.access_token) return null;
    if (session.expires_at && session.expires_at * 1000 < Date.now() + 60000) {
      localStorage.removeItem(ADMIN_SESSION_KEY);
      return null;
    }
    return session;
  } catch {
    return null;
  }
}

function saveConfirmations() {
  if (isAdminActive()) {
    schedulePersistRemoteState();
  } else {
    localStorage.setItem(SIMULATION_STORAGE_KEY, JSON.stringify(state.simulationConfirmations));
  }
}

function saveReleases() {
  if (isAdminActive()) {
    schedulePersistRemoteState();
  } else {
    localStorage.setItem(SIMULATION_RELEASE_STORAGE_KEY, JSON.stringify(state.simulationReleases));
  }
}

function saveStateReleases() {
  if (isAdminActive()) {
    schedulePersistRemoteState();
  } else {
    localStorage.setItem(SIMULATION_STATE_RELEASE_STORAGE_KEY, JSON.stringify(state.simulationStateReleases));
  }
}

function remotePayload() {
  return {
    confirmations: state.remoteConfirmations,
    releases: state.remoteReleases,
    stateReleases: state.remoteStateReleases,
    updatedAt: new Date().toISOString(),
  };
}

function normalizeRemotePayload(payload) {
  return {
    confirmations: payload?.confirmations && typeof payload.confirmations === "object" ? payload.confirmations : {},
    releases: payload?.releases && typeof payload.releases === "object" ? payload.releases : {},
    stateReleases: payload?.stateReleases && typeof payload.stateReleases === "object" ? payload.stateReleases : {},
  };
}

function applyRemotePayload(payload) {
  const normalized = normalizeRemotePayload(payload);
  const signature = JSON.stringify(normalized);
  const changed = signature !== state.remoteStateSignature;
  state.remoteStateSignature = signature;
  state.remoteConfirmations = normalized.confirmations;
  state.remoteReleases = normalized.releases;
  state.remoteStateReleases = normalized.stateReleases;
  syncEffectiveDecisionState();
  return changed;
}

function cloneDecisionMap(value) {
  return JSON.parse(JSON.stringify(value || {}));
}

function ensureCategory(root, key) {
  if (!root[key]) root[key] = {};
  return root[key];
}

function removeRegionalReleaseForAthlete(releases, key, code) {
  Object.values(releases[key] || {}).forEach((regionalMap) => {
    if (regionalMap) delete regionalMap[code];
  });
}

function eachRemoteReleaseCode(key, callback) {
  Object.entries(state.remoteReleases[key] || {}).forEach(([regionalId, regionalMap]) => {
    Object.keys(regionalMap || {}).forEach((code) => callback(code, regionalId));
  });
  Object.keys(state.remoteStateReleases[key] || {}).forEach((code) => callback(code, "state"));
}

function syncEffectiveDecisionState() {
  if (isAdminActive()) {
    state.confirmations = state.remoteConfirmations;
    state.releases = state.remoteReleases;
    state.stateReleases = state.remoteStateReleases;
    return;
  }

  const confirmations = cloneDecisionMap(state.simulationConfirmations);
  const releases = cloneDecisionMap(state.simulationReleases);
  const stateReleases = cloneDecisionMap(state.simulationStateReleases);
  const keys = new Set([
    ...Object.keys(state.remoteConfirmations),
    ...Object.keys(state.remoteReleases),
    ...Object.keys(state.remoteStateReleases),
  ]);

  keys.forEach((key) => {
    ensureCategory(confirmations, key);
    ensureCategory(releases, key);
    ensureCategory(stateReleases, key);

    eachRemoteReleaseCode(key, (code, regionalId) => {
      delete confirmations[key][code];
      if (regionalId === "state") {
        stateReleases[key][code] = true;
      } else {
        if (!releases[key][regionalId]) releases[key][regionalId] = {};
        releases[key][regionalId][code] = true;
      }
    });

    Object.entries(state.remoteConfirmations[key] || {}).forEach(([code, regionalId]) => {
      delete stateReleases[key][code];
      removeRegionalReleaseForAthlete(releases, key, code);
      confirmations[key][code] = regionalId;
    });
  });

  state.confirmations = confirmations;
  state.releases = releases;
  state.stateReleases = stateReleases;
}

function activeConfirmationsForCategory(key) {
  return ensureCategory(isAdminActive() ? state.remoteConfirmations : state.simulationConfirmations, key);
}

function activeReleasesForCategory(key) {
  return ensureCategory(isAdminActive() ? state.remoteReleases : state.simulationReleases, key);
}

function activeStateReleasesForCategory(key) {
  return ensureCategory(isAdminActive() ? state.remoteStateReleases : state.simulationStateReleases, key);
}

function activeRegionalReleases(regionalId) {
  const releases = activeReleasesForCategory(state.selectedCategory);
  if (!releases[regionalId]) releases[regionalId] = {};
  return releases[regionalId];
}

function isOfficialDecisionForAthlete(athleteCode, key = state.selectedCategory) {
  const code = String(athleteCode);
  if (state.remoteConfirmations[key]?.[code]) return true;
  if (state.remoteStateReleases[key]?.[code]) return true;
  return Object.values(state.remoteReleases[key] || {}).some((regionalMap) => Boolean(regionalMap?.[code]));
}

function remoteUrl(path) {
  return `${ADMIN_CONFIG.supabaseUrl.replace(/\/$/, "")}${path}`;
}

async function remoteRequest(path, options = {}) {
  const token = options.authenticated ? state.admin.session?.access_token : SUPABASE_PUBLIC_KEY;
  const headers = {
    apikey: SUPABASE_PUBLIC_KEY,
    Authorization: `Bearer ${token || SUPABASE_PUBLIC_KEY}`,
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...(options.headers || {}),
  };

  const response = await fetch(remoteUrl(path), {
    ...options,
    headers,
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `HTTP ${response.status}`);
  }

  return response;
}

async function loadRemoteState() {
  if (!state.admin.configured) return;
  try {
    const query = `/rest/v1/panel_state?id=eq.${encodeURIComponent(REMOTE_STATE_ID)}&select=payload`;
    const response = await remoteRequest(query);
    const rows = await response.json();
    if (rows[0]?.payload) {
      return applyRemotePayload(rows[0].payload);
    }
  } catch (error) {
    console.warn("Nao foi possivel carregar o estado remoto.", error);
  }
  return false;
}

function startRemoteStateRefresh() {
  if (!state.admin.configured || remoteRefreshTimer) return;
  remoteRefreshTimer = setInterval(async () => {
    if (isAdminActive() || document.hidden) return;
    const changed = await loadRemoteState();
    if (changed) render();
  }, REMOTE_REFRESH_INTERVAL_MS);
}

function schedulePersistRemoteState() {
  if (!state.admin.configured || !state.admin.session) return;
  clearTimeout(persistTimer);
  persistTimer = setTimeout(() => {
    persistRemoteState();
  }, 300);
}

async function persistRemoteState() {
  if (!state.admin.configured || !state.admin.session) return;
  state.admin.saving = true;
  renderAdminStatus("Salvando...");
  try {
    await remoteRequest("/rest/v1/panel_state?id=eq.global", {
      authenticated: true,
      method: "PATCH",
      headers: { Prefer: "return=minimal" },
      body: JSON.stringify({
        payload: remotePayload(),
        updated_at: new Date().toISOString(),
      }),
    });
    state.admin.saving = false;
    renderAdminStatus("Salvo");
  } catch (error) {
    state.admin.saving = false;
    if (String(error.message).includes("JWT") || String(error.message).includes("401")) {
      logoutAdmin();
      renderAdminStatus("Sessão expirada");
      return;
    }
    renderAdminStatus("Falha ao salvar");
  }
}

async function loginAdmin(email, password) {
  const response = await remoteRequest("/auth/v1/token?grant_type=password", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  const session = await response.json();
  state.admin.session = session;
  localStorage.setItem(ADMIN_SESSION_KEY, JSON.stringify(session));
  await loadRemoteState();
  render();
}

function logoutAdmin() {
  state.admin.session = null;
  localStorage.removeItem(ADMIN_SESSION_KEY);
  render();
}

function showAdminDialog() {
  els.adminMessage.textContent = state.admin.configured
    ? ""
    : "Login ainda não configurado no ambiente online.";
  if (typeof els.adminDialog.showModal === "function") {
    els.adminDialog.showModal();
  } else {
    els.adminDialog.setAttribute("open", "");
  }
}

function hideAdminDialog() {
  if (typeof els.adminDialog.close === "function") {
    els.adminDialog.close();
  } else {
    els.adminDialog.removeAttribute("open");
  }
}

function isAdminActive() {
  return Boolean(state.admin.session);
}

function renderAdminStatus(message = "") {
  document.body.classList.toggle("is-admin", isAdminActive());
  els.adminToggle.textContent = isAdminActive() ? "Sair" : "Entrar";
  els.adminStatus.hidden = !isAdminActive() && !message;
  els.adminStatus.textContent = message || (isAdminActive() ? "Admin ativo" : "");
}

function categoryConfirmations() {
  return confirmationsForCategory(state.selectedCategory);
}

function confirmationsForCategory(key) {
  if (!state.confirmations[key]) {
    state.confirmations[key] = {};
  }
  return state.confirmations[key];
}

function categoryReleases() {
  return releasesForCategory(state.selectedCategory);
}

function releasesForCategory(key) {
  if (!state.releases[key]) {
    state.releases[key] = {};
  }
  return state.releases[key];
}

function stateReleasesForCategory(key) {
  if (!state.stateReleases[key]) {
    state.stateReleases[key] = {};
  }
  return state.stateReleases[key];
}

function selectedStateReleases() {
  return stateReleasesForCategory(state.selectedCategory);
}

function regionalReleases(regionalId) {
  const releases = categoryReleases();
  if (!releases[regionalId]) {
    releases[regionalId] = {};
  }
  return releases[regionalId];
}

function athleteAppearsInRanking(ranking, athleteCode) {
  return Boolean(ranking?.athletes?.some((athlete) => athleteIdentity(athlete) === String(athleteCode)));
}

function athleteReleaseTargets(athleteCode) {
  const code = String(athleteCode);
  const stateRanking = selectedStateRanking();
  const regionalIds = selectedRankings()
    .filter((ranking) => athleteAppearsInRanking(ranking, code))
    .map((ranking) => ranking.regionalId);

  return {
    hasState: athleteAppearsInRanking(stateRanking, code),
    regionalIds,
  };
}

function isReleasedAcrossCategory(athleteCode) {
  const code = String(athleteCode);
  const targets = athleteReleaseTargets(code);
  const stateReleased = !targets.hasState || Boolean(selectedStateReleases()[code]);
  const regionalReleased = targets.regionalIds.every((regionalId) => Boolean(categoryReleases()[regionalId]?.[code]));

  return stateReleased && regionalReleased;
}

function setCategoryWideRelease(athleteCode, released) {
  const code = String(athleteCode);
  const targets = athleteReleaseTargets(code);
  const stateReleases = activeStateReleasesForCategory(state.selectedCategory);
  const releases = activeReleasesForCategory(state.selectedCategory);
  const confirmations = activeConfirmationsForCategory(state.selectedCategory);

  if (targets.hasState) {
    if (released) {
      stateReleases[code] = true;
    } else {
      delete stateReleases[code];
    }
  }

  targets.regionalIds.forEach((regionalId) => {
    if (!releases[regionalId]) releases[regionalId] = {};
    if (released) {
      releases[regionalId][code] = true;
    } else {
      delete releases[regionalId][code];
    }
  });

  if (released) {
    delete confirmations[code];
  }

  saveConfirmations();
  saveStateReleases();
  saveReleases();
  render();
}

function toggleStateRelease(athleteCode) {
  setCategoryWideRelease(athleteCode, !isReleasedAcrossCategory(athleteCode));
}

function toggleConfirmation(regionalId, athleteCode) {
  const confirmations = activeConfirmationsForCategory(state.selectedCategory);
  const releases = activeRegionalReleases(regionalId);
  const currentRegional = confirmations[athleteCode];

  if (currentRegional === regionalId) {
    delete confirmations[athleteCode];
  } else {
    delete releases[athleteCode];
    confirmations[athleteCode] = regionalId;
  }

  saveConfirmations();
  saveReleases();
  render();
}

function toggleRelease(regionalId, athleteCode) {
  setCategoryWideRelease(athleteCode, !isReleasedAcrossCategory(athleteCode));
}

function formatDate(value) {
  if (!value) return "Dados pendentes";
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function fillFilters() {
  fillCategories();
}

function fillCategories() {
  const categories = uniqueBy(
    state.rankings,
    categoryKey,
  ).sort((a, b) => {
    const groupOrder = ["Subs", "Idades", "Tecnicas"];
    const groupDiff = groupOrder.indexOf(a.categoryGroup) - groupOrder.indexOf(b.categoryGroup);
    if (groupDiff !== 0) return groupDiff;
    return categoryLabel(a).localeCompare(categoryLabel(b), "pt-BR");
  });

  els.categoryFilter.replaceChildren(...categories.map((item) => option(categoryKey(item), categoryLabel(item))));

  if (!categories.some((item) => categoryKey(item) === state.selectedCategory)) {
    state.selectedCategory = categoryKey(categories[0] || {});
  }
  els.categoryFilter.value = state.selectedCategory;
}

function selectedRankings() {
  return rankingsForCategory(state.selectedCategory);
}

function selectedStateRanking() {
  return stateRankingForCategory(state.selectedCategory);
}

function athleteIdentity(athlete) {
  return String(athlete.athleteCode || athlete.name).trim();
}

function stateLimitForCategory(ranking) {
  return ranking?.categoryGroup === "Tecnicas" ? 6 : 4;
}

function isTechnicalRanking(ranking) {
  return ranking?.categoryGroup === "Tecnicas";
}

function athletesThroughCutoff(athletes, limit) {
  if (athletes.length <= limit) return athletes;

  const cutoff = athletes[limit - 1];
  return athletes.filter(
    (athlete, index) =>
      index < limit ||
      (athlete.position === cutoff.position && athlete.points === cutoff.points),
  );
}

function athletesByListLimit(athletes, limit) {
  return athletes.slice(0, limit);
}

function athletesThroughDistinctPositions(athletes, positionLimit) {
  const positions = [];

  athletes.forEach((athlete) => {
    if (!positions.includes(athlete.position) && positions.length < positionLimit) {
      positions.push(athlete.position);
    }
  });

  const allowedPositions = new Set(positions);
  return athletes.filter((athlete) => allowedPositions.has(athlete.position));
}

function stateQualifiedAthletes(stateRanking, releaseCodes = new Set()) {
  if (!stateRanking) return [];
  const candidates = stateRanking.athletes.filter((athlete) => !releaseCodes.has(athleteIdentity(athlete)));
  return athletesByListLimit(candidates, stateLimitForCategory(stateRanking));
}

function stateFederationAthletes(stateRanking, releaseCodes = new Set()) {
  if (!stateRanking) return [];
  const candidates = stateRanking.athletes.filter((athlete) => !releaseCodes.has(athleteIdentity(athlete)));
  return athletesByListLimit(candidates, 2);
}

function stateFinalsAthletes(stateRanking, releaseCodes = new Set()) {
  const federationCodes = stateFederationCodes(stateRanking, releaseCodes);
  return stateQualifiedAthletes(stateRanking, releaseCodes).filter((athlete) => !federationCodes.has(athleteIdentity(athlete)));
}

function regionalFinalsAthletes(ranking, stateCodes = new Set(), releases = {}) {
  if (!isTechnicalRanking(ranking) || ranking.rankingScope !== "regional") return [];
  const candidates = ranking.athletes.filter(
    (athlete) =>
      !isStateQualified(athlete, stateCodes) &&
      !isManuallyReleased(athlete, ranking, releases),
  );
  return athletesByListLimit(candidates, 2);
}

function regionalFinalsRegionalsByAthlete(rankings, stateCodes = new Set(), releases = {}) {
  const regionalsByAthlete = new Map();

  rankings.forEach((ranking) => {
    regionalFinalsAthletes(ranking, stateCodes, releases).forEach((athlete) => {
      const key = athleteIdentity(athlete);
      if (!regionalsByAthlete.has(key)) regionalsByAthlete.set(key, new Set());
      regionalsByAthlete.get(key).add(ranking.regionalId);
    });
  });

  return new Map(
    Array.from(regionalsByAthlete.entries()).map(([key, regionals]) => [
      key,
      Array.from(regionals).sort((a, b) => Number(a) - Number(b)),
    ]),
  );
}

function regionalFinalsCodesForRankings(rankings, stateCodes = new Set(), releases = {}) {
  return new Set(regionalFinalsRegionalsByAthlete(rankings, stateCodes, releases).keys());
}

function stateQualifiedCodes(stateRanking, releaseCodes = new Set()) {
  return new Set(stateQualifiedAthletes(stateRanking, releaseCodes).map(athleteIdentity));
}

function stateFederationCodes(stateRanking, releaseCodes = new Set()) {
  return new Set(stateFederationAthletes(stateRanking, releaseCodes).map(athleteIdentity));
}

function stateClassificationLabel(athlete, stateCodes, federationCodes) {
  const identity = athleteIdentity(athlete);
  if (!stateCodes.has(identity)) return "";
  return federationCodes.has(identity) ? "Copa Federações" : "Finals Copa - via Estadual";
}

function isStateQualified(athlete, stateCodes) {
  return stateCodes.has(athleteIdentity(athlete));
}

function isRegionalFinalsQualified(athlete, regionalFinalsCodes) {
  return regionalFinalsCodes.has(athleteIdentity(athlete));
}

function isConfirmedElsewhere(athlete, ranking, confirmations) {
  const confirmedRegional = confirmations[athleteIdentity(athlete)];
  return Boolean(confirmedRegional && confirmedRegional !== ranking.regionalId);
}

function isManuallyReleased(athlete, ranking, releases) {
  return Boolean(releases[ranking.regionalId]?.[athleteIdentity(athlete)]);
}

function activeCandidatesForRanking(
  ranking,
  confirmations,
  releases,
  stateCodes = new Set(),
  regionalFinalsCodes = new Set(),
) {
  return ranking.athletes.filter(
    (athlete) =>
      !isStateQualified(athlete, stateCodes) &&
      !isRegionalFinalsQualified(athlete, regionalFinalsCodes) &&
      !isConfirmedElsewhere(athlete, ranking, confirmations) &&
      !isManuallyReleased(athlete, ranking, releases),
  );
}

function qualifiedForRanking(
  ranking,
  confirmations = categoryConfirmations(),
  releases = categoryReleases(),
  stateCodes = new Set(),
  regionalFinalsCodes = new Set(),
) {
  const candidates = activeCandidatesForRanking(ranking, confirmations, releases, stateCodes, regionalFinalsCodes);
  if (candidates.length <= QUALIFIED_LIMIT) return candidates;

  return athletesThroughCutoff(candidates, QUALIFIED_LIMIT);
}

function tiedCutoffCodesForRanking(
  ranking,
  confirmations = categoryConfirmations(),
  releases = categoryReleases(),
  stateCodes = new Set(),
  regionalFinalsCodes = new Set(),
) {
  const candidates = activeCandidatesForRanking(ranking, confirmations, releases, stateCodes, regionalFinalsCodes);
  if (candidates.length <= QUALIFIED_LIMIT) return new Set();

  const cutoff = candidates[QUALIFIED_LIMIT - 1];
  const tiedAtCutoff = candidates.filter(
    (athlete) => athlete.position === cutoff.position && athlete.points === cutoff.points,
  );
  const crossesCutoff = tiedAtCutoff.some((athlete) => candidates.indexOf(athlete) >= QUALIFIED_LIMIT);

  if (!crossesCutoff || tiedAtCutoff.length < 2) return new Set();
  return new Set(tiedAtCutoff.map(athleteIdentity));
}

function qualifiedAthletes(
  rankings,
  confirmations = categoryConfirmations(),
  releases = categoryReleases(),
  stateCodes = new Set(),
  regionalFinalsCodes = new Set(),
) {
  return rankings.flatMap((ranking) =>
    qualifiedForRanking(ranking, confirmations, releases, stateCodes, regionalFinalsCodes).map((athlete) => ({ ...athlete, regionalId: ranking.regionalId })),
  );
}

function duplicateQualifiedRegionals(
  rankings,
  confirmations = categoryConfirmations(),
  releases = categoryReleases(),
  stateCodes = new Set(),
  regionalFinalsCodes = new Set(),
) {
  const regionalsByAthlete = new Map();
  qualifiedAthletes(rankings, confirmations, releases, stateCodes, regionalFinalsCodes).forEach((athlete) => {
    const key = athleteIdentity(athlete);
    if (!regionalsByAthlete.has(key)) regionalsByAthlete.set(key, new Set());
    regionalsByAthlete.get(key).add(athlete.regionalId);
  });

  return new Map(
    Array.from(regionalsByAthlete.entries())
      .filter(([, regionals]) => regionals.size > 1)
      .map(([key, regionals]) => [key, Array.from(regionals).sort((a, b) => Number(a) - Number(b))]),
  );
}

function filteredAthletes(ranking) {
  return ranking.athletes;
}

function qualifiedCodesForRanking(
  ranking,
  confirmations = categoryConfirmations(),
  releases = categoryReleases(),
  stateCodes = new Set(),
  regionalFinalsCodes = new Set(),
) {
  return new Set(qualifiedForRanking(ranking, confirmations, releases, stateCodes, regionalFinalsCodes).map(athleteIdentity));
}

function athleteRow(
  athlete,
  ranking,
  qualifiedCodes,
  tiedCutoffCodes,
  duplicateRegionals,
  confirmations,
  releases,
  stateCodes,
  federationCodes,
  regionalFinalsCodes,
  regionalFinalsRegionals,
) {
  const row = document.createElement("div");
  const identity = athleteIdentity(athlete);
  const confirmedRegional = confirmations[identity];
  const isAlreadyStateQualified = isStateQualified(athlete, stateCodes);
  const regionalFinalsSource = regionalFinalsRegionals.get(identity) || [];
  const isAlreadyRegionalFinalsQualified = !isAlreadyStateQualified && regionalFinalsCodes.has(identity);
  const isRegionalFinalsHere = regionalFinalsSource.includes(ranking.regionalId);
  const stateLabelText = stateClassificationLabel(athlete, stateCodes, federationCodes);
  const isReleasedManually = isManuallyReleased(athlete, ranking, releases);
  const isConfirmedHere = !isAlreadyStateQualified && !isAlreadyRegionalFinalsQualified && confirmedRegional === ranking.regionalId;
  const isReleasedElsewhere =
    !isAlreadyStateQualified &&
    !isAlreadyRegionalFinalsQualified &&
    Boolean(confirmedRegional && confirmedRegional !== ranking.regionalId);
  const isQualified = qualifiedCodes.has(identity);
  const isTiedCutoff = isQualified && tiedCutoffCodes.has(identity);
  const duplicateRegionalIds = isQualified ? duplicateRegionals.get(identity) : null;
  const isDuplicate = Boolean(duplicateRegionalIds);
  const canConfirm =
    !isAlreadyStateQualified &&
    !isAlreadyRegionalFinalsQualified &&
    !isReleasedManually &&
    (isQualified || isConfirmedHere);
  const canRelease =
    !isAlreadyStateQualified &&
    (isQualified || isConfirmedHere || isReleasedManually || isAlreadyRegionalFinalsQualified);
  row.className = [
    "athlete-row",
    isQualified ? "is-qualified" : "",
    isTiedCutoff ? "is-tied-cutoff" : "",
    isConfirmedHere ? "is-confirmed" : "",
    isReleasedElsewhere ? "is-released" : "",
    isAlreadyStateQualified ? "is-state-qualified" : "",
    isRegionalFinalsHere ? "is-regional-finals-qualified" : "",
    isReleasedManually ? "is-manual-release" : "",
  ].filter(Boolean).join(" ");

  const tieLabel = isTiedCutoff
    ? `<span class="tie-badge" title="Empate no corte de classificação">Empate</span>`
    : "";
  const duplicateLabel = isDuplicate
    ? `<span class="duplicate-regionals" title="Classificado nas regionais ${duplicateRegionalIds.join(", ")}">(${duplicateRegionalIds.join(", ")})</span>`
    : "";
  const releasedLabel = isReleasedElsewhere
    ? `<span class="released-badge" title="Confirmado na regional ${confirmedRegional}">Confirmado ${confirmedRegional}</span>`
    : "";
  const stateLabel = stateLabelText
    ? `<span class="state-badge" title="Atleta já classificado pelo ranking estadual">${stateLabelText}</span>`
    : "";
  const regionalFinalsLabel = isAlreadyRegionalFinalsQualified
    ? `<span class="regional-finals-badge" title="Classificado para Finals Copa pelo ranking regional técnico">Finals Copa - via Regional${regionalFinalsSource.length ? ` (${regionalFinalsSource.join(", ")})` : ""}</span>`
    : "";
  const manualReleaseLabel = isReleasedManually
    ? `<span class="manual-release-badge" title="Vaga liberada manualmente nesta categoria">Vaga liberada</span>`
    : "";
  const showControls = isAdminActive() || !isOfficialDecisionForAthlete(identity);
  const controls = !showControls
    ? ""
    : isAlreadyStateQualified
      ? `
      <button
        class="release-button"
        type="button"
        data-regional-id="${ranking.regionalId}"
        data-athlete-code="${athlete.athleteCode}"
        aria-pressed="false"
        title="Liberar vaga deste atleta em toda a categoria"
      >×</button>
      <span class="state-lock" title="Classificado pelo ranking estadual">E</span>
    `
      : isAlreadyRegionalFinalsQualified
        ? `
        <button
          class="release-button"
          type="button"
          data-regional-id="${ranking.regionalId}"
          data-athlete-code="${athlete.athleteCode}"
          aria-pressed="false"
          title="Liberar vaga deste atleta em toda a categoria"
        >×</button>
        <span class="regional-finals-lock" title="Classificado para Finals Copa pelo ranking regional">FC</span>
      `
        : `
        <button
          class="confirm-button"
          type="button"
          data-regional-id="${ranking.regionalId}"
          data-athlete-code="${athlete.athleteCode}"
          aria-pressed="${isConfirmedHere ? "true" : "false"}"
          title="${isConfirmedHere ? "Remover confirmação" : "Confirmar vaga nesta regional"}"
          ${canConfirm ? "" : "disabled"}
        >✓</button>
        <button
          class="release-button"
          type="button"
          data-regional-id="${ranking.regionalId}"
          data-athlete-code="${athlete.athleteCode}"
          aria-pressed="${isReleasedManually ? "true" : "false"}"
          title="${isReleasedManually ? "Desfazer liberação da vaga em toda a categoria" : "Liberar vaga deste atleta em toda a categoria"}"
          ${canRelease ? "" : "disabled"}
        >×</button>
      `;

  row.innerHTML = `
    <span class="rank-cell">
      ${controls}
      <span class="rank-position">${athlete.position}</span>
    </span>
    <span class="athlete-main">
      <span class="athlete-name">
        <span class="athlete-name-text">${athlete.name}</span>
        <span class="athlete-meta-line">
          <span class="athlete-inline-code">${athlete.athleteCode}</span>
          ${duplicateLabel}
        </span>
      </span>
      ${tieLabel}
      ${releasedLabel}
      ${stateLabel}
      ${regionalFinalsLabel}
      ${manualReleaseLabel}
    </span>
    <span class="athlete-points">${athlete.points.toLocaleString("pt-BR")}</span>
  `;
  return row;
}

function regionalPanel(
  ranking,
  duplicateRegionals,
  confirmations,
  releases,
  stateCodes,
  federationCodes,
  regionalFinalsCodes,
  regionalFinalsRegionals,
) {
  const panel = document.createElement("article");
  panel.className = "regional-panel";
  const athletes = filteredAthletes(ranking);
  const qualified = qualifiedForRanking(ranking, confirmations, releases, stateCodes, regionalFinalsCodes).length;
  const qualifiedCodes = qualifiedCodesForRanking(ranking, confirmations, releases, stateCodes, regionalFinalsCodes);
  const tiedCutoffCodes = tiedCutoffCodesForRanking(ranking, confirmations, releases, stateCodes, regionalFinalsCodes);

  const rows = athletes.map((athlete) =>
    athleteRow(
      athlete,
      ranking,
      qualifiedCodes,
      tiedCutoffCodes,
      duplicateRegionals,
      confirmations,
      releases,
      stateCodes,
      federationCodes,
      regionalFinalsCodes,
      regionalFinalsRegionals,
    ),
  );
  const body = document.createElement("div");
  body.className = "regional-list";
  body.replaceChildren(...rows);

  const sourceUrl = ranking.url;
  panel.innerHTML = `
    <header class="regional-panel-header">
      <div>
        <h3><a href="${sourceUrl}" target="_blank" rel="noreferrer" aria-label="Abrir fonte ${ranking.regionalLabel}">${ranking.regionalLabel}</a></h3>
        <p>${ranking.athleteCount} atletas · ${qualified} classificados</p>
      </div>
    </header>
  `;
  panel.append(body);

  if (rows.length === 0) {
    const empty = document.createElement("div");
    empty.className = "regional-empty";
    empty.textContent = "Sem atletas para a busca.";
    body.append(empty);
  }

  return panel;
}

function stateAthleteRow(athlete, stateCodes, federationCodes, releaseCodes) {
  const row = document.createElement("div");
  const identity = athleteIdentity(athlete);
  const isReleased = releaseCodes.has(identity);
  const isQualified = stateCodes.has(identity);
  const isFederation = isQualified && federationCodes.has(identity);
  const isFinalsState = isQualified && !isFederation;
  const isGuaranteedFederation = Boolean(athlete.stateTop2Guaranteed);
  const canRelease = isQualified || isReleased;
  row.className = [
    "athlete-row",
    isQualified ? "is-state-panel-qualified" : "",
    isFederation ? "is-federation-cup" : "",
    isFinalsState ? "is-finals-cup" : "",
    isReleased ? "is-manual-release" : "",
    isGuaranteedFederation ? "is-guaranteed-federation" : "",
  ].filter(Boolean).join(" ");
  const label = isFederation ? "Copa Federações" : isFinalsState ? "Finals Copa - via Estadual" : "";
  const status = label
    ? `<span class="${isFederation ? "federation-badge" : "state-badge"}">${label}</span>`
    : "";
  const releasedLabel = isReleased
    ? `<span class="manual-release-badge" title="Vaga liberada no ranking estadual">Vaga liberada</span>`
    : "";
  const showControls = isAdminActive() || !isOfficialDecisionForAthlete(identity);
  const controls = showControls
    ? `
      <button
        class="release-button state-release-button"
        type="button"
        data-athlete-code="${athlete.athleteCode}"
        aria-pressed="${isReleased ? "true" : "false"}"
        title="${isReleased ? "Desfazer liberação em toda a categoria" : "Liberar vaga deste atleta em toda a categoria"}"
        ${canRelease ? "" : "disabled"}
      >×</button>
      ${isQualified ? `<span class="state-lock" title="Classificado pelo ranking estadual">E</span>` : ""}
    `
    : "";
  row.innerHTML = `
    ${isGuaranteedFederation ? `<span class="guaranteed-federation-dot" title="Vaga matematicamente garantida na Copa das Federações"></span>` : ""}
    <span class="rank-cell">
      ${controls}
      <span class="rank-position">${athlete.position}</span>
    </span>
    <span class="athlete-main">
      <span class="athlete-name">
        <span class="athlete-name-text">${athlete.name}</span>
        <span class="athlete-meta-line">
          <span class="athlete-inline-code">${athlete.athleteCode}</span>
        </span>
      </span>
      ${status}
      ${releasedLabel}
    </span>
    <span class="athlete-points">${athlete.points.toLocaleString("pt-BR")}</span>
  `;
  return row;
}

function statePanel(stateRanking, releaseCodes = new Set()) {
  const panel = document.createElement("article");
  panel.className = "regional-panel state-panel";

  if (!stateRanking) {
    panel.innerHTML = `
      <header class="regional-panel-header">
        <div>
          <h3>Estadual</h3>
          <p>Sem ranking estadual</p>
        </div>
      </header>
      <div class="regional-empty">Ranking estadual não encontrado.</div>
    `;
    return panel;
  }

  const qualified = stateQualifiedAthletes(stateRanking, releaseCodes);
  const stateCodes = stateQualifiedCodes(stateRanking, releaseCodes);
  const federationCodes = stateFederationCodes(stateRanking, releaseCodes);
  const body = document.createElement("div");
  body.className = "regional-list";
  body.replaceChildren(...stateRanking.athletes.map((athlete) => stateAthleteRow(athlete, stateCodes, federationCodes, releaseCodes)));

  panel.innerHTML = `
    <header class="regional-panel-header">
      <div>
        <h3><a href="${stateRanking.url}" target="_blank" rel="noreferrer" aria-label="Abrir fonte Estadual">Estadual</a></h3>
        <p>${stateRanking.athleteCount} atletas · ${qualified.length} classificados</p>
      </div>
    </header>
  `;
  panel.append(body);
  return panel;
}

function summaryAthleteRow(athlete, meta, tone = "regional") {
  const row = document.createElement("div");
  row.className = `summary-athlete-row summary-${tone}`;
  row.innerHTML = `
    <span class="rank-position">${athlete.position}</span>
    <span class="athlete-main">
      <span class="athlete-name">${athlete.name}</span>
      <span class="athlete-code">${meta} · Cod. ${athlete.athleteCode}</span>
    </span>
    <span class="athlete-points">${athlete.points.toLocaleString("pt-BR")}</span>
  `;
  return row;
}

function regionalFinalsEntriesForCategory(key, stateCodes = new Set(), releases = {}) {
  const entries = new Map();

  rankingsForCategory(key).forEach((ranking) => {
    regionalFinalsAthletes(ranking, stateCodes, releases).forEach((athlete) => {
      const identity = athleteIdentity(athlete);
      if (stateCodes.has(identity)) return;

      if (!entries.has(identity)) {
        entries.set(identity, {
          athlete,
          regionals: new Set(),
        });
      }
      entries.get(identity).regionals.add(ranking.regionalId);
    });
  });

  return Array.from(entries.values())
    .map((entry) => ({
      athlete: entry.athlete,
      regionals: Array.from(entry.regionals).sort((a, b) => Number(a) - Number(b)),
    }))
    .sort((a, b) => {
      const positionDiff = a.athlete.position - b.athlete.position;
      if (positionDiff !== 0) return positionDiff;
      return a.athlete.name.localeCompare(b.athlete.name, "pt-BR");
    });
}

function summaryCategoryCard(category, rows, emptyText) {
  const card = document.createElement("article");
  card.className = "summary-category-card";
  const body = document.createElement("div");
  body.className = "summary-list";

  if (rows.length) {
    body.replaceChildren(...rows);
  } else {
    const empty = document.createElement("div");
    empty.className = "regional-empty";
    empty.textContent = emptyText;
    body.append(empty);
  }

  card.innerHTML = `
    <header class="summary-category-header">
      <div>
        <p>${category.categoryGroup}</p>
        <h3>${categoryLabel(category)}</h3>
      </div>
      <strong>${rows.length}</strong>
    </header>
  `;
  card.append(body);
  return card;
}

function groupLabel(group) {
  return group === "Tecnicas" ? "Técnicas" : group;
}

function genderInitial(gender) {
  return gender === "Feminina" ? "F" : "M";
}

function genderClass(gender) {
  return gender === "Feminina" ? "gender-f" : "gender-m";
}

function stateRankingFor(group, label, gender) {
  return state.rankings.find(
    (item) =>
      item.rankingScope === "state" &&
      item.categoryGroup === group &&
      item.categoryLabel === label &&
      item.gender === gender,
  );
}

function compactAthleteRow(athlete, gender, tone = "federation") {
  const row = document.createElement("div");
  row.className = `compact-athlete-row compact-${tone}`;
  row.innerHTML = `
    <span class="compact-position">${athlete.position}</span>
    <span class="compact-gender ${genderClass(gender)}">${genderInitial(gender)}</span>
    <span class="compact-athlete-name">${athlete.name}</span>
    <span class="compact-points">${athlete.points.toLocaleString("pt-BR")}</span>
  `;
  return row;
}

function compactCategoryBlock(group, label, tone = "federation") {
  const block = document.createElement("article");
  block.className = "compact-category-block";
  const body = document.createElement("div");
  body.className = "compact-athlete-list";
  const links = [];
  const rows = [];

  ["Feminina", "Masculina"].forEach((gender) => {
    const ranking = stateRankingFor(group, label, gender);
    if (!ranking) return;

    links.push(
      `<a class="${genderClass(gender)}" href="${ranking.url}" target="_blank" rel="noreferrer" title="Abrir ranking ${gender} ${label} na FPT" aria-label="Abrir ranking ${gender} ${label} na FPT">${genderInitial(gender)}</a>`,
    );
    stateFederationAthletes(ranking, new Set(Object.keys(stateReleasesForCategory(categoryKey(ranking))))).forEach((athlete) => {
      rows.push(compactAthleteRow(athlete, gender, tone));
    });
  });

  if (rows.length) {
    body.replaceChildren(...rows);
  } else {
    const empty = document.createElement("div");
    empty.className = "regional-empty";
    empty.textContent = "Sem classificados.";
    body.append(empty);
  }

  block.innerHTML = `
    <header class="compact-category-header">
      <h4>${label}</h4>
      <span class="compact-links">${links.join("")}</span>
    </header>
  `;
  block.append(body);
  block.dataset.count = String(rows.length);
  return block;
}

function compactSummarySection(title, categories) {
  const section = document.createElement("section");
  section.className = "compact-summary-section";
  const blocks = categories.map((category) => compactCategoryBlock(category.group, category.label));
  const total = blocks.reduce((sum, block) => sum + Number(block.dataset.count || 0), 0);
  const body = document.createElement("div");
  body.className = "compact-summary-list";
  body.replaceChildren(...blocks);
  section.innerHTML = `
    <header class="compact-summary-header">
      <h3>${title}</h3>
      <span>${total} atletas</span>
    </header>
  `;
  section.append(body);
  return section;
}

function groupedSummarySections(cardsByGroup) {
  const groupOrder = ["Subs", "Idades", "Tecnicas"];
  return groupOrder
    .filter((group) => cardsByGroup.has(group))
    .map((group) => {
      const section = document.createElement("section");
      section.className = "summary-group-section";
      const cards = cardsByGroup.get(group);
      const total = cards.reduce((sum, card) => sum + Number(card.dataset.count || 0), 0);
      const grid = document.createElement("div");
      grid.className = "summary-group-cards";
      grid.replaceChildren(...cards);
      section.innerHTML = `
        <header class="summary-group-header">
          <h3>${groupLabel(group)}</h3>
          <span>${total} atletas</span>
        </header>
      `;
      section.append(grid);
      return section;
    });
}

function renderFederationView() {
  const sections = [
    compactSummarySection("Subs", [
      { group: "Subs", label: "Sub 12" },
      { group: "Subs", label: "Sub 14" },
      { group: "Subs", label: "Sub 16" },
    ]),
    compactSummarySection("Idades", [
      { group: "Idades", label: "40+" },
      { group: "Idades", label: "50+" },
      { group: "Idades", label: "60+" },
    ]),
    compactSummarySection("Técnicas A+B+C", [
      { group: "Tecnicas", label: "A" },
      { group: "Tecnicas", label: "B" },
      { group: "Tecnicas", label: "C" },
    ]),
    compactSummarySection("Técnicas D+E", [
      { group: "Tecnicas", label: "D" },
      { group: "Tecnicas", label: "E" },
    ]),
  ];
  els.federationGrid.replaceChildren(...sections);
}

function renderFinalsView() {
  const cardsByGroup = new Map();
  allCategories().forEach((category) => {
    const key = categoryKey(category);
    const stateRanking = stateRankingForCategory(key);
    const stateReleaseCodes = new Set(Object.keys(stateReleasesForCategory(key)));
    const regionalReleases = releasesForCategory(key);
    const stateCodes = stateQualifiedCodes(stateRanking, stateReleaseCodes);
    const rows = stateFinalsAthletes(stateRanking, stateReleaseCodes).map((athlete) =>
      summaryAthleteRow(athlete, "Finals Copa - via Estadual", "state"),
    );
    regionalFinalsEntriesForCategory(key, stateCodes, regionalReleases).forEach((entry) => {
      rows.push(
        summaryAthleteRow(
          entry.athlete,
          `Finals Copa - via Regional ${entry.regionals.join(", ")}`,
          "regional-finals",
        ),
      );
    });
    const card = summaryCategoryCard(category, rows, "Sem classificados para Finals Copa.");
    card.dataset.count = String(rows.length);
    if (!cardsByGroup.has(category.categoryGroup)) cardsByGroup.set(category.categoryGroup, []);
    cardsByGroup.get(category.categoryGroup).push(card);
  });
  els.finalsGrid.replaceChildren(...groupedSummarySections(cardsByGroup));
}

function setActiveView(view) {
  state.activeView = view;
  els.viewTabs.forEach((tab) => tab.classList.toggle("is-active", tab.dataset.view === view));
  els.viewPanels.forEach((panel) => panel.classList.toggle("is-active", panel.dataset.viewPanel === view));
  els.regionalToolbar.hidden = view !== "regionals";
  render();
}

function render() {
  syncEffectiveDecisionState();
  const rankings = selectedRankings();
  const stateRanking = selectedStateRanking();
  const stateReleaseCodes = new Set(Object.keys(selectedStateReleases()));
  const stateCodes = stateQualifiedCodes(stateRanking, stateReleaseCodes);
  const federationCodes = stateFederationCodes(stateRanking, stateReleaseCodes);
  const confirmations = categoryConfirmations();
  const releases = categoryReleases();
  const regionalFinalsRegionals = regionalFinalsRegionalsByAthlete(rankings, stateCodes, releases);
  const regionalFinalsCodes = regionalFinalsCodesForRankings(rankings, stateCodes, releases);
  const duplicateRegionals = duplicateQualifiedRegionals(rankings, confirmations, releases, stateCodes, regionalFinalsCodes);
  els.updatedAt.textContent = `Atualizado em ${formatDate(state.data?.generatedAt)}`;

  if (rankings.length === 0) {
    els.selectedMeta.textContent = "";
    els.selectedMeta.hidden = true;
    els.selectedTitle.textContent = "Rankings regionais";
    els.regionalGrid.replaceChildren();
    els.emptyState.hidden = false;
    renderAdminStatus();
    return;
  }

  els.selectedMeta.textContent = "";
  els.selectedMeta.hidden = true;
  els.selectedTitle.textContent = categoryLabel(rankings[0]);
  els.regionalGrid.replaceChildren(
    statePanel(stateRanking, stateReleaseCodes),
    ...rankings.map((ranking) =>
      regionalPanel(
        ranking,
        duplicateRegionals,
        confirmations,
        releases,
        stateCodes,
        federationCodes,
        regionalFinalsCodes,
        regionalFinalsRegionals,
      ),
    ),
  );
  els.emptyState.hidden = true;
  renderFederationView();
  renderFinalsView();
  renderAdminStatus();
}

function bindEvents() {
  els.categoryFilter.addEventListener("change", (event) => {
    state.selectedCategory = event.target.value;
    render();
  });

  els.viewTabs.forEach((tab) => {
    tab.addEventListener("click", () => setActiveView(tab.dataset.view));
  });

  els.adminToggle.addEventListener("click", () => {
    if (isAdminActive()) {
      logoutAdmin();
    } else {
      showAdminDialog();
    }
  });

  els.adminCancel.addEventListener("click", hideAdminDialog);

  els.adminLoginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.admin.configured) {
      els.adminMessage.textContent = "Login ainda não configurado no ambiente online.";
      return;
    }

    els.adminMessage.textContent = "Entrando...";
    try {
      await loginAdmin(els.adminEmail.value, els.adminPassword.value);
      els.adminPassword.value = "";
      hideAdminDialog();
      renderAdminStatus("Admin ativo");
    } catch {
      els.adminMessage.textContent = "E-mail ou senha inválidos.";
    }
  });

  els.regionalGrid.addEventListener("click", (event) => {
    const stateReleaseButton = event.target.closest(".state-release-button");
    if (stateReleaseButton && !stateReleaseButton.disabled) {
      toggleStateRelease(stateReleaseButton.dataset.athleteCode);
      return;
    }

    const confirmButton = event.target.closest(".confirm-button");
    if (confirmButton && !confirmButton.disabled) {
      toggleConfirmation(confirmButton.dataset.regionalId, confirmButton.dataset.athleteCode);
      return;
    }

    const releaseButton = event.target.closest(".release-button");
    if (releaseButton && !releaseButton.disabled) {
      toggleRelease(releaseButton.dataset.regionalId, releaseButton.dataset.athleteCode);
    }
  });

  document.addEventListener("visibilitychange", async () => {
    if (document.hidden || isAdminActive()) return;
    const changed = await loadRemoteState();
    if (changed) render();
  });
}

async function boot() {
  try {
    const response = await fetch("data/rankings.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    state.rankings = state.data.rankings || [];
    await loadRemoteState();
    fillFilters();
    bindEvents();
    render();
    startRemoteStateRefresh();
    renderAdminStatus();
  } catch (error) {
    els.updatedAt.textContent = "Falha ao carregar dados";
    els.updatedAt.classList.add("error");
    els.emptyState.hidden = false;
    els.emptyState.textContent = `Não foi possível carregar docs/data/rankings.json. ${error.message}`;
  }
}

boot();
