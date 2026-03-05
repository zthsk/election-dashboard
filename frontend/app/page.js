"use client";

import { useEffect, useMemo, useState } from "react";
import { ComposableMap, Geographies, Geography } from "react-simple-maps";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Area,
  AreaChart,
} from "recharts";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";
const ELECTIONS = ["2082", "2079"];

const I18N = {
  en: {
    title: "Nepal Election Live Results",
    subtitle: "Multi-election dashboard with constituency history",
    election: "Election",
    updatedAsOf: "Results updated as of",
    lastChecked: "Last checked",
    stale: "Updates delayed",
    fresh: "Live",
    filters: "Filters",
    state: "State",
    district: "District",
    search: "Search",
    allStates: "All states",
    allDistricts: "All districts",
    allConstituencies: "All constituencies",
    clear: "Clear",
    partySummary: "Party Summary",
    results: "Results",
    analytics: "Analytics",
    compare: "Comparison",
    updates1h: "Updates (1h)",
    updates24h: "Updates (24h)",
    totalVotes: "Total votes",
    rows: "Rows",
    districtMap: "District Map",
    constituencyMap: "Constituency Map",
    mapModeDistrict: "Districts",
    mapModeConstituency: "Constituencies",
    mapHint: "Click a district to filter results",
    candidate: "Candidate",
    party: "Party",
    votes: "Votes",
    status: "Status",
    symbol: "Symbol",
    prevWinner: "Prev winner",
    constituency: "Constituency",
    districtCol: "District",
    page: "Page",
    noData: "No data yet. Poller is running and waiting for changes.",
    retained: "Retained constituencies",
    flips: "Party flips",
  },
  ne: {
    title: "नेपाल निर्वाचन लाइभ नतिजा",
    subtitle: "बहु-निर्वाचन ड्यासबोर्ड र अघिल्लो विजेता तुलना",
    election: "निर्वाचन",
    updatedAsOf: "नतिजा अद्यावधिक भएको समय",
    lastChecked: "अन्तिम जाँच",
    stale: "अपडेट ढिलो छ",
    fresh: "लाइभ",
    filters: "फिल्टर",
    state: "प्रदेश",
    district: "जिल्ला",
    search: "खोज",
    allStates: "सबै प्रदेश",
    allDistricts: "सबै जिल्ला",
    allConstituencies: "सबै निर्वाचन क्षेत्र",
    clear: "खाली गर्नुहोस्",
    partySummary: "दल सारांश",
    results: "नतिजा",
    analytics: "विश्लेषण",
    compare: "तुलना",
    updates1h: "अपडेट (१ घण्टा)",
    updates24h: "अपडेट (२४ घण्टा)",
    totalVotes: "कुल मत",
    rows: "रेकर्ड",
    districtMap: "जिल्ला नक्सा",
    constituencyMap: "निर्वाचन क्षेत्र नक्सा",
    mapModeDistrict: "जिल्ला",
    mapModeConstituency: "निर्वाचन क्षेत्र",
    mapHint: "फिल्टर गर्न जिल्लामा क्लिक गर्नुहोस्",
    candidate: "उम्मेदवार",
    party: "दल",
    votes: "मत",
    status: "स्थिति",
    symbol: "चिन्ह",
    prevWinner: "अघिल्लो विजेता",
    constituency: "निर्वाचन क्षेत्र",
    districtCol: "जिल्ला",
    page: "पृष्ठ",
    noData: "अहिले डेटा छैन। पोलर चलिरहेको छ र अपडेट पर्खिरहेको छ।",
    retained: "उही दल दोहोरिएको क्षेत्र",
    flips: "दल परिवर्तन भएका क्षेत्र",
  },
};

function normalizeText(input) {
  return String(input || "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

function nptFormat(ts) {
  if (!ts) return "-";
  try {
    const value = new Date(ts);
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Kathmandu",
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(value);
  } catch {
    return ts;
  }
}

function relativeAge(seconds) {
  if (seconds == null) return "-";
  if (seconds < 60) return `${seconds}s ago`;
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  return `${hours}h ${mins % 60}m ago`;
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

function DistrictMap({
  mapMode,
  districts,
  constituencies,
  selectedDistrictId,
  selectedConstituencyId,
  onSelectDistrict,
  onSelectConstituency,
  text,
}) {
  const [districtGeo, setDistrictGeo] = useState(null);
  const [constituencyGeo, setConstituencyGeo] = useState(null);

  useEffect(() => {
    let active = true;
    async function loadGeo() {
      try {
        if (mapMode === "constituency") {
          if (constituencyGeo) return;
          const response = await fetch("/data/nepal-constituencies.geojson", { cache: "force-cache" });
          if (!response.ok) {
            throw new Error(`GeoJSON not found: ${response.status}`);
          }
          const constituency = await response.json();
          const validConstituency =
            constituency &&
            constituency.type === "FeatureCollection" &&
            Array.isArray(constituency.features) &&
            constituency.features.length > 0;
          if (active && validConstituency) {
            setConstituencyGeo(constituency);
          }
        } else {
          if (districtGeo) return;
          const response = await fetch("/data/nepal-districts.geojson", { cache: "force-cache" });
          if (!response.ok) {
            throw new Error(`GeoJSON not found: ${response.status}`);
          }
          const district = await response.json();
          const validDistrict =
            district &&
            district.type === "FeatureCollection" &&
            Array.isArray(district.features) &&
            district.features.length > 0;
          if (active && validDistrict) {
            setDistrictGeo(district);
          }
        }
      } catch {
        if (mapMode === "constituency") {
          setConstituencyGeo(null);
        } else {
          setDistrictGeo(null);
        }
      }
    }
    loadGeo();
    return () => {
      active = false;
    };
  }, [mapMode, districtGeo, constituencyGeo]);

  const nameToId = useMemo(() => {
    const map = new Map();
    for (const row of districts || []) {
      if (row.name) {
        map.set(normalizeText(row.name), String(row.id));
      }
      if (row.name_en) {
        map.set(normalizeText(row.name_en), String(row.id));
      }
    }
    return map;
  }, [districts]);

  const geoData = mapMode === "constituency" ? constituencyGeo : districtGeo;

  if (!geoData) {
    const items = mapMode === "constituency" ? constituencies || [] : districts || [];
    return (
      <div className="tile-map">
        {items.map((item) => {
          const isConstituency = mapMode === "constituency";
          const selected = isConstituency
            ? String(selectedDistrictId || "") === String(item.district_id || "") &&
              String(selectedConstituencyId || "") === String(item.id || "")
            : String(selectedDistrictId || "") === String(item.id);
          return (
            <button
              type="button"
              key={isConstituency ? item.key || `${item.district_id}-${item.id}` : item.id}
              className={selected ? "tile active" : "tile"}
              onClick={() => {
                if (isConstituency) {
                  onSelectConstituency({
                    districtId: selected ? "" : String(item.district_id || ""),
                    constituencyId: selected ? "" : String(item.id || ""),
                  });
                } else {
                  onSelectDistrict(selected ? "" : String(item.id));
                }
              }}
            >
              {isConstituency ? `${item.district_name || ""} ${item.id || ""}` : item.name}
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div className="district-map">
      <ComposableMap projection="geoMercator" projectionConfig={{ scale: 4200, center: [84, 28] }}>
        <Geographies geography={geoData}>
          {({ geographies }) =>
            geographies.map((geo) => {
              const props = geo.properties || {};
              const districtName =
                props.DISTRICT || props.DIST_EN || props.DIST_NAME || props.NAME || props.name || "";
              const matchedDistrictId =
                nameToId.get(normalizeText(districtName)) || String(props.DIST_CODE || props.id || "");
              const constituencyCode = props.CON ? String(props.CON) : "";

              const isSelected =
                mapMode === "constituency"
                  ? matchedDistrictId &&
                    constituencyCode &&
                    String(selectedDistrictId || "") === String(matchedDistrictId) &&
                    String(selectedConstituencyId || "") === String(constituencyCode)
                  : matchedDistrictId && String(selectedDistrictId || "") === String(matchedDistrictId);

              return (
                <Geography
                  key={geo.rsmKey}
                  geography={geo}
                  onClick={() => {
                    if (!matchedDistrictId) return;
                    if (mapMode === "constituency") {
                      if (!constituencyCode) return;
                      onSelectConstituency({
                        districtId: isSelected ? "" : String(matchedDistrictId),
                        constituencyId: isSelected ? "" : String(constituencyCode),
                      });
                    } else {
                      onSelectDistrict(isSelected ? "" : String(matchedDistrictId));
                    }
                  }}
                  style={{
                    default: {
                      fill: isSelected ? "#f97316" : "#e2e8f0",
                      stroke: "#334155",
                      strokeWidth: mapMode === "constituency" ? 0.2 : 0.5,
                      outline: "none",
                    },
                    hover: {
                      fill: "#fb923c",
                      stroke: "#1e293b",
                      strokeWidth: mapMode === "constituency" ? 0.4 : 0.8,
                      outline: "none",
                    },
                    pressed: {
                      fill: "#ea580c",
                      outline: "none",
                    },
                  }}
                />
              );
            })
          }
        </Geographies>
      </ComposableMap>
      <p className="hint">{text.mapHint}</p>
    </div>
  );
}

export default function HomePage() {
  const [lang, setLang] = useState("en");
  const text = I18N[lang] || I18N.en;

  const [electionId, setElectionId] = useState("2082");
  const [meta, setMeta] = useState(null);
  const [party, setParty] = useState([]);
  const [summary, setSummary] = useState(null);
  const [series, setSeries] = useState([]);
  const [compare, setCompare] = useState(null);
  const [states, setStates] = useState([]);
  const [districts, setDistricts] = useState([]);
  const [constituencies, setConstituencies] = useState([]);
  const [results, setResults] = useState([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");

  const [stateId, setStateId] = useState("");
  const [districtId, setDistrictId] = useState("");
  const [constituencyId, setConstituencyId] = useState("");
  const [constituencyKey, setConstituencyKey] = useState("");
  const [mapMode, setMapMode] = useState("district");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);

  const pageSize = 25;

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const election = params.get("election");
    if (election && ELECTIONS.includes(election)) {
      setElectionId(election);
    }
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    params.set("election", electionId);
    window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
    setStateId("");
    setDistrictId("");
    setConstituencyId("");
    setConstituencyKey("");
    setMapMode("district");
    setQ("");
    setPage(1);
  }, [electionId]);

  useEffect(() => {
    setDistrictId("");
    setConstituencyId("");
    setConstituencyKey("");
    setPage(1);
  }, [stateId]);

  useEffect(() => {
    setConstituencyId("");
    setConstituencyKey("");
    setPage(1);
  }, [districtId]);

  useEffect(() => {
    async function loadCore() {
      try {
        const queryElection = `election_id=${encodeURIComponent(electionId)}`;
        const comparePrevious = electionId === "2082" ? "2079" : "none";
        const [
          metaData,
          partyData,
          summaryData,
          seriesData,
          statesData,
          districtsData,
          constituenciesData,
          compareData,
        ] =
          await Promise.all([
            fetchJson(`${API_BASE}/meta?${queryElection}`),
            fetchJson(`${API_BASE}/party?${queryElection}`),
            fetchJson(`${API_BASE}/analytics/summary?${queryElection}`),
            fetchJson(`${API_BASE}/analytics/timeseries?${queryElection}&metric=changes&window=24h`),
            fetchJson(`${API_BASE}/lookups/states?${queryElection}`),
            fetchJson(
              `${API_BASE}/lookups/districts?${queryElection}${
                stateId ? `&state_id=${encodeURIComponent(stateId)}` : ""
              }`
            ),
            fetchJson(
              `${API_BASE}/lookups/constituencies?${queryElection}${
                districtId ? `&district_id=${encodeURIComponent(districtId)}` : ""
              }${stateId ? `&state_id=${encodeURIComponent(stateId)}` : ""}`
            ),
            fetchJson(
              `${API_BASE}/analytics/compare?current=${encodeURIComponent(electionId)}&previous=${comparePrevious}`
            ),
          ]);

        setMeta(metaData);
        setParty(partyData.items || []);
        setSummary(summaryData);
        setSeries(seriesData.points || []);
        setStates(statesData.items || []);
        setDistricts(districtsData.items || []);
        setConstituencies(constituenciesData.items || []);
        setCompare(compareData);
        setError("");
      } catch (err) {
        setError(String(err));
      }
    }

    loadCore();
    const timer = setInterval(loadCore, 20000);
    return () => clearInterval(timer);
  }, [electionId, stateId, districtId]);

  useEffect(() => {
    async function loadResults() {
      try {
        const params = new URLSearchParams();
        params.set("election_id", electionId);
        params.set("page", String(page));
        params.set("page_size", String(pageSize));
        if (stateId) params.set("state_id", stateId);
        if (districtId) params.set("district_id", districtId);
        if (constituencyId) params.set("constituency_id", constituencyId);
        if (constituencyKey) params.set("constituency_key", constituencyKey);
        if (q.trim()) params.set("q", q.trim());

        const data = await fetchJson(`${API_BASE}/results?${params.toString()}`);
        setResults(data.items || []);
        setTotal(data.total || 0);
        setError("");
      } catch (err) {
        setError(String(err));
      }
    }

    loadResults();
    const timer = setInterval(loadResults, 15000);
    return () => clearInterval(timer);
  }, [electionId, stateId, districtId, constituencyId, constituencyKey, q, page]);

  const maxPage = Math.max(1, Math.ceil(total / pageSize));

  const topPartiesChart = party.slice(0, 10).map((row) => ({
    party: row.party,
    votes: row.total_votes,
    elected: row.elected_count,
    leading: row.leading_count,
  }));

  const partyDeltas = (compare?.party_deltas || []).slice(0, 8);

  return (
    <main className="shell">
      <header className="hero">
        <div>
          <h1>{text.title}</h1>
          <p>{text.subtitle}</p>
        </div>
        <div className="lang-toggle">
          <button type="button" onClick={() => setLang("en")} className={lang === "en" ? "active" : ""}>
            EN
          </button>
          <button type="button" onClick={() => setLang("ne")} className={lang === "ne" ? "active" : ""}>
            नेपाली
          </button>
        </div>
      </header>

      <section className="status-bar">
        <div>
          <strong>{text.election}:</strong>{" "}
          {ELECTIONS.map((id) => (
            <button
              key={id}
              type="button"
              className={id === electionId ? "chip active" : "chip"}
              onClick={() => setElectionId(id)}
            >
              {id}
            </button>
          ))}
        </div>
        <div>
          <strong>{text.updatedAsOf}:</strong> {meta?.results_updated_at_npt || nptFormat(meta?.results_updated_at)} (
          {relativeAge(meta?.results_age_seconds)})
        </div>
        <div>
          <strong>{text.lastChecked}:</strong> {meta?.last_polled_at_npt || nptFormat(meta?.last_polled_at)}
        </div>
        <div className={meta?.freshness_status === "stale" ? "badge stale" : "badge fresh"}>
          {meta?.freshness_status === "stale" ? text.stale : text.fresh}
        </div>
      </section>

      {error ? <div className="error">{error}</div> : null}

      <section className="cards">
        <article>
          <h3>{text.rows}</h3>
          <p>{summary?.rows_count ?? 0}</p>
        </article>
        <article>
          <h3>{text.totalVotes}</h3>
          <p>{summary?.total_votes?.toLocaleString?.() || 0}</p>
        </article>
        <article>
          <h3>{text.updates1h}</h3>
          <p>{summary?.updates_last_1h ?? 0}</p>
        </article>
        <article>
          <h3>{text.updates24h}</h3>
          <p>{summary?.updates_last_24h ?? 0}</p>
        </article>
      </section>

      <section className="grid two">
        <article className="panel">
          <h2>{text.filters}</h2>
          <div className="filters">
            <label>
              {text.state}
              <select value={stateId} onChange={(e) => setStateId(e.target.value)}>
                <option value="">{text.allStates}</option>
                {states.map((state) => (
                  <option key={state.id} value={state.id}>
                    {state.name}
                  </option>
                ))}
              </select>
            </label>

            <label>
              {text.district}
              <select value={districtId} onChange={(e) => setDistrictId(e.target.value)}>
                <option value="">{text.allDistricts}</option>
                {districts.map((district) => (
                  <option key={district.id} value={district.id}>
                    {district.name}
                  </option>
                ))}
              </select>
            </label>

            <label>
              {text.constituency}
              <select
                value={constituencyKey}
                onChange={(e) => {
                  const value = e.target.value;
                  setConstituencyKey(value);
                  if (!value) {
                    setConstituencyId("");
                    return;
                  }
                  const matched = constituencies.find((item) => String(item.key) === String(value));
                  setConstituencyId(matched?.id ? String(matched.id) : "");
                }}
              >
                <option value="">{text.allConstituencies}</option>
                {constituencies.map((item) => (
                  <option key={item.key || `${item.district_id}-${item.id}`} value={item.key || ""}>
                    {(item.district_name ? `${item.district_name} ` : "") + (item.name || item.id)}
                  </option>
                ))}
              </select>
            </label>

            <label>
              {text.search}
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={text.search} />
            </label>

            <button
              type="button"
              onClick={() => {
                setStateId("");
                setDistrictId("");
                setConstituencyId("");
                setConstituencyKey("");
                setQ("");
                setPage(1);
              }}
            >
              {text.clear}
            </button>
          </div>
        </article>

        <article className="panel">
          <h2>{mapMode === "constituency" ? text.constituencyMap : text.districtMap}</h2>
          <div className="lang-toggle">
            <button
              type="button"
              onClick={() => setMapMode("district")}
              className={mapMode === "district" ? "active" : ""}
            >
              {text.mapModeDistrict}
            </button>
            <button
              type="button"
              onClick={() => setMapMode("constituency")}
              className={mapMode === "constituency" ? "active" : ""}
            >
              {text.mapModeConstituency}
            </button>
          </div>
          <DistrictMap
            mapMode={mapMode}
            districts={districts}
            constituencies={constituencies}
            selectedDistrictId={districtId}
            selectedConstituencyId={constituencyId}
            onSelectDistrict={(id) => {
              setDistrictId(id);
              if (!id) {
                setConstituencyId("");
                setConstituencyKey("");
              }
              setPage(1);
            }}
            onSelectConstituency={({ districtId: nextDistrict, constituencyId: nextConstituency }) => {
              setDistrictId(nextDistrict);
              setConstituencyId(nextConstituency);
              const matched = constituencies.find(
                (item) =>
                  String(item.district_id || "") === String(nextDistrict || "") &&
                  String(item.id || "") === String(nextConstituency || "")
              );
              setConstituencyKey(matched?.key ? String(matched.key) : "");
              setPage(1);
            }}
            text={text}
          />
        </article>
      </section>

      <section className="grid two">
        <article className="panel">
          <h2>{text.partySummary}</h2>
          <div className="chart-box">
            <ResponsiveContainer width="100%" height={320}>
              <BarChart data={topPartiesChart} margin={{ top: 8, right: 12, bottom: 8, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="party" interval={0} angle={-20} textAnchor="end" height={80} />
                <YAxis />
                <Tooltip />
                <Legend />
                <Bar dataKey="votes" fill="#0f766e" />
                <Bar dataKey="leading" fill="#f59e0b" />
                <Bar dataKey="elected" fill="#ea580c" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </article>

        <article className="panel">
          <h2>{text.analytics}</h2>
          <div className="chart-box">
            <ResponsiveContainer width="100%" height={320}>
              <AreaChart data={series} margin={{ top: 8, right: 12, bottom: 8, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="ts" tick={{ fontSize: 11 }} />
                <YAxis />
                <Tooltip />
                <Area type="monotone" dataKey="value" stroke="#1d4ed8" fill="#93c5fd" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </article>
      </section>

      <section className="panel">
        <h2>{text.compare}</h2>
        <div className="cards">
          <article>
            <h3>{text.retained}</h3>
            <p>{compare?.retained_constituencies ?? 0}</p>
          </article>
          <article>
            <h3>{text.flips}</h3>
            <p>{(compare?.constituency_flips || []).length}</p>
          </article>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{text.party}</th>
                <th>Vote Delta</th>
                <th>Elected Delta</th>
              </tr>
            </thead>
            <tbody>
              {partyDeltas.map((row) => (
                <tr key={row.party}>
                  <td>{row.party}</td>
                  <td>{row.vote_delta}</td>
                  <td>{row.elected_delta}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2>{text.results}</h2>
        {results.length === 0 ? (
          <p>{text.noData}</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{text.candidate}</th>
                  <th>{text.party}</th>
                  <th>{text.symbol}</th>
                  <th>{text.votes}</th>
                  <th>{text.status}</th>
                  <th>{text.prevWinner}</th>
                  <th>{text.districtCol}</th>
                  <th>{text.constituency}</th>
                </tr>
              </thead>
              <tbody>
                {results.map((row) => (
                  <tr key={row.id}>
                    <td>{row.candidate || "-"}</td>
                    <td>{row.party || "-"}</td>
                    <td>{row.party_symbol_name || "-"}</td>
                    <td>{row.votes ?? "-"}</td>
                    <td>{row.status || "-"}</td>
                    <td>
                      {row.prev_winner_party
                        ? `${row.prev_winner_party} (${row.prev_winner_candidate || "-"})`
                        : "-"}
                    </td>
                    <td>{row.district_name || row.district_id || "-"}</td>
                    <td>{row.constituency_name || row.constituency_id || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="pagination">
          <button type="button" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}>
            Prev
          </button>
          <span>
            {text.page} {page} / {maxPage}
          </span>
          <button
            type="button"
            onClick={() => setPage((p) => Math.min(maxPage, p + 1))}
            disabled={page >= maxPage}
          >
            Next
          </button>
        </div>
      </section>
    </main>
  );
}
