const API_BASE_URL = "https://in-sai.onrender.com";

/* =========================================================
   TALENTREACH — TALENT POOL
   ========================================================= */


/* ---------------------------------------------------------
   Bulk selection state
--------------------------------------------------------- */

let currentCandidates = [];
const selectedCandidateIds = new Set();


/* ---------------------------------------------------------
   Load Talent Pool
--------------------------------------------------------- */

async function loadTalentPool() {

    const loading =
        document.getElementById("poolLoading");

    const results =
        document.getElementById("talentPoolResults");

    const empty =
        document.getElementById("poolEmpty");

    const count =
        document.getElementById("poolCount");

    const status =
        document.getElementById("poolStatus");


    if (!results) {
        console.error("Talent Pool results container not found.");
        return;
    }


    try {

        if (loading) {
            loading.classList.remove("hidden");
        }

        if (empty) {
            empty.classList.add("hidden");
        }


        status.textContent =
            "Loading saved candidates...";


        const response =
            await fetch(`${API_BASE_URL}/candidates`);

console.log("Talent Pool response status:", response.status);
console.log("Talent Pool response URL:", response.url);


        if (!response.ok) {

            throw new Error(
                "Unable to load candidates from server."
            );

        }


        const candidates =
            await response.json();


        console.log(
            "Talent Pool candidates:",
            candidates
        );


        if (loading) {
            loading.classList.add("hidden");
        }


        if (count) {
            count.textContent =
                candidates.length;
        }


        /* -------------------------------------------------
           No candidates
        ------------------------------------------------- */

        if (candidates.length === 0) {

            results.innerHTML = "";

            if (empty) {
                empty.classList.remove("hidden");
            }

            status.textContent =
                "No saved candidates";

            return;
        }


        if (empty) {
            empty.classList.add("hidden");
        }


        status.textContent =
            `${candidates.length} candidate${candidates.length === 1 ? "" : "s"} saved`;


        renderCandidates(candidates);


    } catch (error) {

        console.error(
            "Talent Pool error:",
            error
        );


        if (loading) {
            loading.classList.add("hidden");
        }


        status.textContent =
            "Unable to load Talent Pool";


        results.innerHTML = `

            <div class="empty-state">

                <div class="empty-icon">
                    ⚠
                </div>

                <h3>
                    Something went wrong
                </h3>

                <p>
                    ${escapeHtml(error.message)}
                </p>

            </div>

        `;
    }
}


/* ---------------------------------------------------------
   Render candidates
--------------------------------------------------------- */

function renderCandidates(candidates) {

    const results =
        document.getElementById(
            "talentPoolResults"
        );


    results.innerHTML = "";


    currentCandidates = candidates;
    selectedCandidateIds.clear();
    updateBulkToolbar();


    candidates.forEach(candidate => {

        const card =
            document.createElement("div");


        card.className =
            "candidate-card";


        const initials =
            getInitials(
                candidate.name
            );


        /* -------------------------------------------------
           Skills
        ------------------------------------------------- */

        let skillsHTML = "";


        if (candidate.skills) {

            const skills =
                candidate.skills
                    .split(",")
                    .map(skill => skill.trim())
                    .filter(Boolean);


            skillsHTML =
                skills.map(skill => {

                    return `
                        <span class="skill">
                            ${escapeHtml(skill)}
                        </span>
                    `;

                }).join("");

        }


        /* -------------------------------------------------
           Location
        ------------------------------------------------- */

        const locationParts = [

            candidate.city,

            candidate.state,

            candidate.region

        ].filter(Boolean);


        const location =
            locationParts.length
                ? locationParts.join(", ")
                : (
                    candidate.location ||
                    "Location not specified"
                );


        /* -------------------------------------------------
           Professional details
        ------------------------------------------------- */

        const role =
            candidate.current_role ||
            "Role not specified";


        const company =
            candidate.company ||
            "Company not specified";


        const experience =
            candidate.experience ||
            "Experience not specified";


        const gender =
            candidate.gender ||
            "Not specified";


        const financeCategory =
            candidate.finance_category ||
            "";


        const financeSubcategory =
            candidate.finance_subcategory ||
            "";


        const status =
            candidate.status ||
            "active";


        /* -------------------------------------------------
           Candidate card
        ------------------------------------------------- */

        card.innerHTML = `

            <div class="candidate-main">

                <label class="candidate-select">
                    <input
                        type="checkbox"
                        class="candidate-select-checkbox"
                        data-candidate-id="${candidate.id}"
                    >
                </label>

                <div class="avatar">
                    ${initials}
                </div>


                <div>

                    <div class="candidate-name">

                        ${escapeHtml(
                            candidate.name ||
                            "Unnamed Candidate"
                        )}

                    </div>


                    <div class="candidate-role">

                        ${escapeHtml(role)}

                        ${
                            candidate.company
                                ? ` • ${escapeHtml(company)}`
                                : ""
                        }

                    </div>


                    <div class="skills">

                        ${skillsHTML}

                    </div>

                </div>

            </div>


            <div class="candidate-actions">

                <div>

                    <div class="match">

                        ${escapeHtml(status)}

                    </div>


                    <div class="source">

                        📍 ${escapeHtml(location)}

                    </div>

                </div>


                <div class="talent-details">

                    <div>
                        <strong>
                            Experience
                        </strong>

                        <span>
                            ${escapeHtml(experience)}
                        </span>
                    </div>


                    <div>
                        <strong>
                            Gender
                        </strong>

                        <span>
                            ${escapeHtml(gender)}
                        </span>
                    </div>


                    ${
                        financeCategory
                        ?
                        `
                        <div>
                            <strong>
                                Finance
                            </strong>

                            <span>
                                ${escapeHtml(
                                    financeCategory
                                )}
                            </span>
                        </div>
                        `
                        :
                        ""
                    }


                    ${
                        financeSubcategory
                        ?
                        `
                        <div>
                            <strong>
                                Specialization
                            </strong>

                            <span>
                                ${escapeHtml(
                                    financeSubcategory
                                )}
                            </span>
                        </div>
                        `
                        :
                        ""
                    }

                </div>


                <div class="candidate-buttons">

                    ${
                        candidate.linkedin_url
                        ?
                        `
                        <a
                            class="view-button"
                            href="${escapeAttribute(
                                candidate.linkedin_url
                            )}"
                            target="_blank"
                            rel="noopener noreferrer"
                        >
                            View Profile →
                        </a>
                        `
                        :
                        ""
                    }


                    <button
                        class="github-button"
                        type="button"
                    >
                        ◉ GitHub Insights
                    </button>


                    <button
                        class="view-button"
                        type="button"
                        onclick="removeCandidate(${candidate.id})"
                    >
                        Remove
                    </button>

                </div>


                <div
                    class="github-result"
                    style="display:none;"
                ></div>

            </div>

        `;


        results.appendChild(card);


        const selectCheckbox =
            card.querySelector(".candidate-select-checkbox");

        if (selectCheckbox) {

            selectCheckbox.addEventListener(
                "change",
                () => {

                    const id = candidate.id;

                    if (selectCheckbox.checked) {
                        selectedCandidateIds.add(id);
                    } else {
                        selectedCandidateIds.delete(id);
                    }

                    updateBulkToolbar();
                }
            );
        }


        const githubButton =
            card.querySelector(".github-button");

        if (githubButton) {

            githubButton.addEventListener(
                "click",
                () => handleGitHubLookup(candidate, githubButton)
            );
        }

    });

}


/* ---------------------------------------------------------
   GitHub Insights lookup (Talent Pool)
--------------------------------------------------------- */

async function handleGitHubLookup(candidate, button) {

    const card =
        button.closest(".candidate-card");

    if (!card) return;

    const githubResult =
        card.querySelector(".github-result");

    if (!githubResult) return;


    /* Toggle off if a result is already showing */

    if (
        githubResult.style.display !== "none" &&
        githubResult.innerHTML.trim()
    ) {

        githubResult.style.display = "none";
        githubResult.innerHTML = "";
        button.textContent = "◉ GitHub Insights";
        button.classList.remove("github-active");
        return;
    }


    githubResult.style.display = "block";

    githubResult.innerHTML = `
        <div class="github-loading">
            <span class="github-spinner"></span>
            Fetching GitHub Insights...
        </div>
    `;

    button.textContent = "Fetching GitHub Insights...";
    button.disabled = true;


    try {

        const skills =
            candidate.skills
                ? candidate.skills
                    .split(",")
                    .map(skill => skill.trim())
                    .filter(Boolean)
                : [];

        const location =
            [candidate.city, candidate.state, candidate.region]
                .filter(Boolean)
                .join(", ") ||
            candidate.location ||
            "";

        const payload = {
            name: candidate.name || "",
            email: candidate.email || null,
            company: candidate.company || null,
            location: location || null,
            current_role: candidate.current_role || null,
            linkedin_url: candidate.linkedin_url || null,
            skills: skills
        };

        const response =
            await fetch(`${API_BASE_URL}/github/lookup`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

        let data = null;

        try {
            data = await response.json();
        } catch {
            data = null;
        }

        if (!response.ok) {
            throw new Error(
                (data && data.message) ||
                `GitHub lookup failed (HTTP ${response.status})`
            );
        }

        if (data && data.status === "found" && data.profile) {

            renderGitHubResult(githubResult, data.profile);
            button.textContent = "✓ GitHub Insights";
            button.classList.add("github-active");
            return;
        }

        if (data && data.status === "possible_matches") {

            renderGitHubMatches(githubResult, data.matches || []);
            button.textContent = "◉ Possible Matches";
            button.classList.add("github-active");
            return;
        }

        renderGitHubNotFound(githubResult, data && data.message);
        button.textContent = "◉ No GitHub Profile";
        button.classList.add("github-active");

    } catch (error) {

        console.error("GitHub lookup error:", error);
        renderGitHubError(githubResult, error.message);
        button.textContent = "◉ Insights Unavailable";
        button.classList.add("github-active");

    } finally {

        button.disabled = false;
    }
}


/* ---------------------------------------------------------
   GitHub Insights rendering (Talent Pool)
--------------------------------------------------------- */

function renderGitHubResult(container, profile) {

    const username = profile.username || "";
    const profileUrl =
        profile.profile_url ||
        (username ? `https://github.com/${encodeURIComponent(username)}` : "");
    const avatar = profile.avatar_url || "";
    const name = profile.name || username || "GitHub Profile";
    const bio = profile.bio || "";
    const company = profile.company || "";
    const location = profile.location || "";
    const email = profile.public_email || "";
    const website = profile.website || "";
    const languages =
        Array.isArray(profile.languages) ? profile.languages.join(", ") : "";
    const projects =
        Array.isArray(profile.projects) ? profile.projects.slice(0, 5) : [];

    const projectsHtml = projects.map(repo => `
        <div class="github-project">
            <div class="github-project-name">
                ${
                    repo.url
                        ? `<a href="${escapeAttribute(repo.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(repo.name || "Repository")}</a>`
                        : escapeHtml(repo.name || "Repository")
                }
            </div>
            ${
                repo.description
                    ? `<div class="github-project-description">${escapeHtml(repo.description)}</div>`
                    : ""
            }
            <div class="github-project-stars">★ ${escapeHtml(String(repo.stars || 0))} stars</div>
        </div>
    `).join("");

    container.innerHTML = `
        <div class="github-panel">
            <div class="github-panel-header">
                ${
                    avatar
                        ? `<img class="github-avatar" src="${escapeAttribute(avatar)}" alt="${escapeAttribute(name)}">`
                        : `<div class="github-avatar github-avatar-placeholder">${escapeHtml(name.charAt(0).toUpperCase())}</div>`
                }
                <div class="github-identity">
                    <div class="github-name">${escapeHtml(name)}</div>
                    ${username ? `<div class="github-username">@${escapeHtml(username)}</div>` : ""}
                    ${
                        profileUrl
                            ? `<a class="github-profile-link" href="${escapeAttribute(profileUrl)}" target="_blank" rel="noopener noreferrer">View GitHub Profile ↗</a>`
                            : ""
                    }
                </div>
            </div>

            ${bio ? `<div class="github-field"><div class="github-field-label">Bio</div><div class="github-field-value">${escapeHtml(bio)}</div></div>` : ""}

            <div class="github-grid">
                ${company ? `<div class="github-stat"><span>Company</span><strong>${escapeHtml(company)}</strong></div>` : ""}
                ${location ? `<div class="github-stat"><span>Location</span><strong>${escapeHtml(location)}</strong></div>` : ""}
                ${email ? `<div class="github-stat"><span>Public Email</span><strong>${escapeHtml(email)}</strong></div>` : ""}
                ${website ? `<div class="github-stat"><span>Website</span><strong>${escapeHtml(website)}</strong></div>` : ""}
                <div class="github-stat"><span>Public Repositories</span><strong>${escapeHtml(String(profile.public_repos ?? 0))}</strong></div>
                <div class="github-stat"><span>Followers</span><strong>${escapeHtml(String(profile.followers ?? 0))}</strong></div>
                <div class="github-stat"><span>Following</span><strong>${escapeHtml(String(profile.following ?? 0))}</strong></div>
                <div class="github-stat"><span>Total Repository Stars</span><strong>${escapeHtml(String(profile.total_stars ?? 0))}</strong></div>
            </div>

            ${languages ? `<div class="github-field"><div class="github-field-label">Languages</div><div class="github-language-list">${escapeHtml(languages)}</div></div>` : ""}

            ${projectsHtml ? `<div class="github-field"><div class="github-field-label">Recent Public Projects</div><div class="github-project-list">${projectsHtml}</div></div>` : ""}

            <div class="github-insight-note">
                GitHub insights are based only on publicly available GitHub profile and repository data.
            </div>
        </div>
    `;
}


function renderGitHubMatches(container, matches) {

    if (!Array.isArray(matches) || matches.length === 0) {
        renderGitHubNotFound(container);
        return;
    }

    const matchHtml = matches.slice(0, 5).map((match, index) => {

        const username = match.username || "";
        const name = match.name || username || `Possible Match ${index + 1}`;
        const url =
            match.profile_url ||
            (username ? `https://github.com/${encodeURIComponent(username)}` : "");
        const avatar = match.avatar_url || "";

        return `
            <div class="github-match">
                <div class="github-match-main">
                    ${
                        avatar
                            ? `<img class="github-match-avatar" src="${escapeAttribute(avatar)}" alt="${escapeAttribute(name)}">`
                            : `<div class="github-match-avatar github-avatar-placeholder">${escapeHtml(name.charAt(0).toUpperCase())}</div>`
                    }
                    <div class="github-match-info">
                        <div class="github-match-name">${escapeHtml(name)}</div>
                        ${username ? `<div class="github-match-username">@${escapeHtml(username)}</div>` : ""}
                        ${match.company ? `<div class="github-match-meta">${escapeHtml(match.company)}</div>` : ""}
                        ${match.location ? `<div class="github-match-meta">${escapeHtml(match.location)}</div>` : ""}
                        ${match.bio ? `<div class="github-match-bio">${escapeHtml(match.bio)}</div>` : ""}
                    </div>
                </div>
                <div class="github-match-actions">
                    ${
                        url
                            ? `<a class="github-check-button" href="${escapeAttribute(url)}" target="_blank" rel="noopener noreferrer">Check Profile</a>`
                            : ""
                    }
                    <button type="button" class="github-verify-button" data-match-index="${index}">
                        Verify Profile
                    </button>
                </div>
            </div>
        `;
    }).join("");

    container.innerHTML = `
        <div class="github-panel github-matches-panel">
            <div class="github-matches-header">
                <strong>Possible GitHub Profiles</strong>
                <span>Verify the correct profile before using its insights.</span>
            </div>
            <div class="github-matches-list">${matchHtml}</div>
        </div>
    `;

    container.querySelectorAll(".github-verify-button").forEach(button => {

        button.addEventListener("click", async () => {

            const index = Number(button.dataset.matchIndex);
            const match = matches[index];
            if (!match) return;

            if (match.username) {

                try {

                    const response = await fetch(`${API_BASE_URL}/github/insights/${encodeURIComponent(match.username)}`);
                    const data = await response.json();

                    if (response.ok && data.status === "success" && data.insights) {
                        renderGitHubResult(container, data.insights);
                        return;
                    }

                } catch (error) {
                    console.error("GitHub verify error:", error);
                }
            }

            renderGitHubResult(container, match);
        });
    });
}


function renderGitHubNotFound(container, message) {

    container.innerHTML = `
        <div class="github-panel github-not-found">
            <div class="github-not-found-title">GitHub profile not found</div>
            <div class="github-not-found-text">
                ${escapeHtml(
                    message ||
                    "No reliable public GitHub profile could be matched to this candidate. No profile has been invented."
                )}
            </div>
        </div>
    `;
}


function renderGitHubError(container, message) {

    container.innerHTML = `
        <div class="github-panel github-error">
            <div class="github-error-title">GitHub lookup could not be completed</div>
            <div class="github-error-text">${escapeHtml(message || "Unknown error")}</div>
        </div>
    `;
}


/* ---------------------------------------------------------
   Remove candidate
--------------------------------------------------------- */

async function removeCandidate(candidateId) {

    const confirmed =
        confirm(
            "Remove this candidate from your Talent Pool?"
        );


    if (!confirmed) {
        return;
    }


    try {

        const response =
            await fetch(
                `${API_BASE_URL}/candidates/${candidateId}`,
                {
                    method: "DELETE"
                }
            );


        if (!response.ok) {

            throw new Error(
                "Could not remove candidate."
            );

        }


        await loadTalentPool();


    } catch (error) {

        console.error(
            "Remove candidate error:",
            error
        );


        alert(
            error.message
        );

    }

}


/* ---------------------------------------------------------
   Get initials
--------------------------------------------------------- */

function getInitials(name) {

    if (!name) {
        return "?";
    }


    return name
        .trim()
        .split(/\s+/)
        .slice(0, 2)
        .map(word =>
            word.charAt(0).toUpperCase()
        )
        .join("");
}


/* ---------------------------------------------------------
   Escape HTML
--------------------------------------------------------- */

function escapeHtml(value) {

    if (value === null || value === undefined) {
        return "";
    }


    const div =
        document.createElement("div");


    div.textContent =
        String(value);


    return div.innerHTML;
}


/* ---------------------------------------------------------
   Escape URL attribute
--------------------------------------------------------- */

function escapeAttribute(value) {

    if (value === null || value === undefined) {
        return "";
    }


    return String(value)

        .replace(/&/g, "&amp;")

        .replace(/"/g, "&quot;")

        .replace(/</g, "&lt;")

        .replace(/>/g, "&gt;");
}


/* ---------------------------------------------------------
   Bulk toolbar
--------------------------------------------------------- */

function getSelectedCandidates() {

    return currentCandidates.filter(candidate =>
        selectedCandidateIds.has(candidate.id)
    );
}


function updateBulkToolbar() {

    const countLabel =
        document.getElementById("bulkSelectedCount");

    const exportButton =
        document.getElementById("exportSelectedCsv");

    const removeButton =
        document.getElementById("removeSelectedCandidates");

    const outreachButton =
        document.getElementById("generateOutreachSelected");

    const selectAllCheckbox =
        document.getElementById("selectAllCandidates");

    const selectedCount = selectedCandidateIds.size;


    if (countLabel) {
        countLabel.textContent =
            `${selectedCount} selected`;
    }


    [exportButton, removeButton, outreachButton].forEach(button => {

        if (button) {
            button.disabled = selectedCount === 0;
        }
    });


    if (selectAllCheckbox) {

        const total = currentCandidates.length;

        selectAllCheckbox.checked =
            total > 0 && selectedCount === total;

        selectAllCheckbox.indeterminate =
            selectedCount > 0 && selectedCount < total;
    }
}


function handleSelectAllChange(checked) {

    selectedCandidateIds.clear();

    if (checked) {

        currentCandidates.forEach(candidate => {
            selectedCandidateIds.add(candidate.id);
        });
    }


    document
        .querySelectorAll(".candidate-select-checkbox")
        .forEach(checkbox => {

            const id =
                Number(checkbox.dataset.candidateId);

            checkbox.checked =
                selectedCandidateIds.has(id);
        });


    updateBulkToolbar();
}


/* ---------------------------------------------------------
   CSV export (selected candidates only)
--------------------------------------------------------- */

function escapeCsvValue(value) {

    if (value === null || value === undefined) {
        return "";
    }

    const stringValue = String(value);

    if (/[",\n]/.test(stringValue)) {

        return `"${stringValue.replace(/"/g, '""')}"`;
    }

    return stringValue;
}


function exportSelectedToCsv() {

    const selected = getSelectedCandidates();

    if (!selected.length) {
        return;
    }


    const columns = [
        { key: "name", label: "Name" },
        { key: "current_role", label: "Role" },
        { key: "company", label: "Company" },
        { key: "experience", label: "Experience" },
        { key: "gender", label: "Gender" },
        { key: "skills", label: "Skills" },
        { key: "finance_category", label: "Finance Category" },
        { key: "finance_subcategory", label: "Specialization" },
        { key: "email", label: "Email" },
        { key: "linkedin_url", label: "LinkedIn" }
    ];


    const rows = selected.map(candidate => {

        const locationParts = [
            candidate.city,
            candidate.state,
            candidate.region
        ].filter(Boolean);

        const location =
            locationParts.length
                ? locationParts.join(", ")
                : (candidate.location || "");

        return [
            ...columns.map(column =>
                escapeCsvValue(candidate[column.key])
            ),
            escapeCsvValue(location)
        ].join(",");
    });


    const header =
        [...columns.map(column => column.label), "Location"]
            .map(escapeCsvValue)
            .join(",");

    const csvContent =
        [header, ...rows].join("\r\n");

    const blob = new Blob(
        [csvContent],
        { type: "text/csv;charset=utf-8;" }
    );

    const url = URL.createObjectURL(blob);

    const link = document.createElement("a");

    link.href = url;
    link.download =
        `talent-pool-selected-${Date.now()}.csv`;

    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    URL.revokeObjectURL(url);
}


/* ---------------------------------------------------------
   Bulk remove
--------------------------------------------------------- */

async function removeSelectedCandidates() {

    const selected = getSelectedCandidates();

    if (!selected.length) {
        return;
    }

    const confirmed = confirm(
        `Remove ${selected.length} selected candidate` +
        `${selected.length === 1 ? "" : "s"} from your Talent Pool?`
    );

    if (!confirmed) {
        return;
    }


    const removeButton =
        document.getElementById("removeSelectedCandidates");

    if (removeButton) {
        removeButton.disabled = true;
        removeButton.textContent = "Removing...";
    }


    const failures = [];

    for (const candidate of selected) {

        try {

            const response = await fetch(
                `${API_BASE_URL}/candidates/${candidate.id}`,
                { method: "DELETE" }
            );

            if (!response.ok) {
                failures.push(candidate.name || candidate.id);
            }

        } catch (error) {

            console.error(
                "Bulk remove error:",
                error
            );

            failures.push(candidate.name || candidate.id);
        }
    }


    if (removeButton) {
        removeButton.disabled = false;
        removeButton.textContent = "Remove Selected";
    }


    if (failures.length) {

        alert(
            `Some candidates could not be removed: ` +
            `${failures.join(", ")}`
        );
    }


    await loadTalentPool();
}


/* ---------------------------------------------------------
   Bulk outreach queue

   NOTE: This queues the selected candidates for outreach
   and hands off to the Outreach page. The Outreach page
   itself needs matching logic to read this queue and
   auto-generate one draft per candidate — that lives in
   outreach.html / its script, which isn't wired up here.
--------------------------------------------------------- */

function generateOutreachForSelected() {

    const selected = getSelectedCandidates();

    if (!selected.length) {
        return;
    }

    const queue = selected.map(candidate => ({
        id: candidate.id,
        name: candidate.name || "",
        email: candidate.email || "",
        current_role: candidate.current_role || "",
        company: candidate.company || "",
        skills: candidate.skills || "",
        linkedin_url: candidate.linkedin_url || ""
    }));

    localStorage.setItem(
        "talentreach_outreach_queue",
        JSON.stringify(queue)
    );

    window.location.href = "/outreach.html";
}


/* ---------------------------------------------------------
   Start Talent Pool
--------------------------------------------------------- */

document.addEventListener(
    "DOMContentLoaded",
    () => {

        loadTalentPool();


        const selectAllCheckbox =
            document.getElementById("selectAllCandidates");

        if (selectAllCheckbox) {

            selectAllCheckbox.addEventListener(
                "change",
                () => handleSelectAllChange(selectAllCheckbox.checked)
            );
        }


        const exportButton =
            document.getElementById("exportSelectedCsv");

        if (exportButton) {

            exportButton.addEventListener(
                "click",
                exportSelectedToCsv
            );
        }


        const removeButton =
            document.getElementById("removeSelectedCandidates");

        if (removeButton) {

            removeButton.addEventListener(
                "click",
                removeSelectedCandidates
            );
        }


        const outreachButton =
            document.getElementById("generateOutreachSelected");

        if (outreachButton) {

            outreachButton.addEventListener(
                "click",
                generateOutreachForSelected
            );
        }

    }
);
