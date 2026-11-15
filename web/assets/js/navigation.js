"use strict";

/* KEYLAND NAVIGATION FALLBACK
   Kept separate from app.js so a failure in an unrelated dashboard feature
   cannot disable page switching. */
(function () {
  const titles = {
    overview: "მიმოხილვა",
    mobile: "მობილური ქსელი",
    hotspot: "Hotspot",
    portal: "Captive Portal",
    activity: "მოწყობილობების აქტივობა",
    repeater: "Wi-Fi გამეორება",
    zerotier: "ZeroTier",
    network: "ქსელის მართვა",
    system: "სისტემა"
  };
  const sectionToView = {
    "overview-section": "overview",
    "mobile-section": "mobile",
    "hotspot-section": "hotspot",
    "portal-section": "portal",
    "activity-section": "activity",
    "repeater-section": "repeater",
    "zerotier-section": "zerotier",
    "network-section": "network",
    "system-section": "system"
  };
  const viewToSection = Object.fromEntries(
    Object.entries(sectionToView).map(([section, view]) => [view, section])
  );

  function normalize(value) {
    const key = String(value || "").replace(/^#/, "");
    return titles[key] ? key : (sectionToView[key] || "overview");
  }

  function render(value) {
    const view = normalize(value);
    document.body.dataset.activeView = view;

    document.querySelectorAll("#dashboard [data-page-view]").forEach(node => {
      const active = node.dataset.pageView === view;
      node.style.setProperty(
        "display",
        active ? (node.classList.contains("section-heading") ? "flex" : "block") : "none",
        "important"
      );
      node.setAttribute("aria-hidden", String(!active));
    });

    const title = document.getElementById("pageTitle");
    if (title) title.textContent = titles[view];

    document.querySelectorAll(".primary-nav a[data-view-target]").forEach(link => {
      const active = link.dataset.viewTarget === view;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });

    const quickStatus = document.getElementById("quickStatus");
    if (quickStatus) {
      quickStatus.style.setProperty("display", view === "overview" ? "grid" : "none", "important");
    }
    return view;
  }

  function navigate(value, addHistory) {
    const view = render(value);
    const hash = "#" + (viewToSection[view] || "overview-section");
    if (location.hash !== hash) {
      try {
        history[addHistory ? "pushState" : "replaceState"]({ keylandView: view }, "", hash);
      } catch (_) {}
    }
  }

  document.addEventListener("click", event => {
    const link = event.target && event.target.closest
      ? event.target.closest(".primary-nav a[data-view-target]")
      : null;
    if (!link) return;
    const view = link.dataset.viewTarget;
    if (!titles[view]) return;
    event.preventDefault();
    event.stopPropagation();
    navigate(view, true);
  }, true);

  window.addEventListener("popstate", () => navigate(location.hash, false));
  window.addEventListener("hashchange", () => navigate(location.hash, false));

  const initialize = () => navigate(location.hash || "overview", false);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
