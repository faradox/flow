// analytics.js

(function () {
	var config = window.FlowMatomoConfig || {};
	var matomoUrl = config.url || "";
	var siteId = config.siteId || "";

	if (matomoUrl && !matomoUrl.endsWith("/")) {
		matomoUrl += "/";
	}

	var _paq = window._paq = window._paq || [];

	if (
		!matomoUrl ||
		!siteId ||
		window.location.href.includes("localhost") ||
		window.location.href.includes("127.0.0.1")
	) {
		return;
	}

	_paq.push(["setDocumentTitle", document.title]);
	_paq.push(["trackPageView"]);
	_paq.push(["enableHeartBeatTimer"]);
	_paq.push(["enableLinkTracking"]);

	_paq.push(["setTrackerUrl", matomoUrl + "matomo.php"]);
	_paq.push(["setSiteId", String(siteId)]);
	const d = document,
		g = d.createElement("script"),
		s = d.getElementsByTagName("script")[0];
	g.type = "text/javascript";
	g.async = true;
	g.defer = true;
	g.src = matomoUrl + "matomo.js";
	s.parentNode.insertBefore(g, s);
})();
