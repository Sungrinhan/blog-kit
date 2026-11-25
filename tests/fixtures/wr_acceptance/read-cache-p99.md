---
title: What a Read Cache Did to p99 Latency
description: Adding a read cache cut p99 latency for search from 420 to 180 milliseconds and for catalog from 310 to 150 milliseconds, while checkout barely moved.
date: 2026-11-20
author: Alex Rivera
canonical: https://notes.example.org/dev-notes/read-cache-p99/
slug: read-cache-p99
site_name: Example Dev Notes
site_url: https://notes.example.org/dev-notes/
author_url: https://notes.example.org/dev-notes/about/
author_same_as: ["https://github.com/example-author", "https://www.linkedin.com/in/example-author"]
lang: en
og_image_alt: Abstract teal gradient banner for an article about cache latency
tags: ["performance", "caching", "latency"]
---

A read cache in front of the catalog database cut p99 latency for search from 420 to 180 milliseconds and for catalog from 310 to 150 milliseconds. Checkout barely moved, from 260 to 240 milliseconds, because most of its time is spent in a payment call that the cache never touches. This note explains how the numbers were measured and why the slowest percentile is the one worth watching.

## Why p99 and not the average

An average hides the requests that hurt. If one request in a hundred waits half a second, the mean barely changes, but the user who waited remembers it. The 99th percentile is the time that 99 out of 100 requests finish within, so it moves when the slow tail moves. That is the number a cache should change if it is doing its job.

## What changed

The cache sits between the API and the catalog database and keeps recent product and search results for sixty seconds. Writes invalidate the affected keys, so a price change shows up on the next read. Nothing else in the request path changed during the measurement window.

<figure class="blog-chart">
<svg viewBox="0 0 560 380" style="max-width: 100%; height: auto; font-family: 'Inter', system-ui, sans-serif;" role="img" aria-labelledby="p99-latency-before-and-after-caching-title p99-latency-before-and-after-caching-desc">
  <style>
    @media (prefers-color-scheme: dark) { .chart-muted { fill: #d1d5db; } }
    @media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
  </style>
  <title id="p99-latency-before-and-after-caching-title">p99 latency before and after caching</title>
  <desc id="p99-latency-before-and-after-caching-desc">Grouped bar chart of p99 latency for three endpoints before and after adding a read cache: search fell from 420 to 180 ms, catalog from 310 to 150 ms, checkout from 260 to 240 ms.</desc>
  <text x="280.0" y="29" text-anchor="middle" font-size="18" font-weight="800" fill="currentColor">p99 latency before and after caching</text>
  <text x="280.0" y="48.0" text-anchor="middle" font-size="12" class="chart-muted" fill="#4b5563"><tspan x="280.0" dy="0">Milliseconds, lower is better</tspan></text>
  <line x1="72" y1="277.0" x2="492" y2="277.0" stroke="currentColor" opacity="0.08" />
<line x1="72" y1="230.8" x2="492" y2="230.8" stroke="currentColor" opacity="0.08" />
<line x1="72" y1="184.5" x2="492" y2="184.5" stroke="currentColor" opacity="0.08" />
<line x1="72" y1="138.2" x2="492" y2="138.2" stroke="currentColor" opacity="0.08" />
<line x1="72" y1="92.0" x2="492" y2="92.0" stroke="currentColor" opacity="0.08" />
<rect x="72" y="62" width="10" height="10" fill="#f97316" />
<text x="87" y="71" font-size="11" fill="currentColor">Before</text>
<rect x="177" y="62" width="10" height="10" fill="#38bdf8" />
<text x="192" y="71" font-size="11" fill="currentColor">After</text>
<rect x="124.0" y="92.0" width="16.0" height="185.0" fill="#f97316" />
<rect x="142.0" y="197.7" width="16.0" height="79.3" fill="#38bdf8" />
<text x="142.0" y="295.0" text-anchor="middle" font-size="10" fill="currentColor" opacity="0.8"><tspan x="142.0" dy="0">search</tspan></text>
<rect x="264.0" y="140.5" width="16.0" height="136.5" fill="#f97316" />
<rect x="282.0" y="210.9" width="16.0" height="66.1" fill="#38bdf8" />
<text x="282.0" y="295.0" text-anchor="middle" font-size="10" fill="currentColor" opacity="0.8"><tspan x="282.0" dy="0">catalog</tspan></text>
<rect x="404.0" y="162.5" width="16.0" height="114.5" fill="#f97316" />
<rect x="422.0" y="171.3" width="16.0" height="105.7" fill="#38bdf8" />
<text x="422.0" y="295.0" text-anchor="middle" font-size="10" fill="currentColor" opacity="0.8"><tspan x="422.0" dy="0">checkout</tspan></text>
<text x="132.0" y="86.0" text-anchor="middle" font-size="10" font-weight="700" fill="currentColor">420</text>
<text x="150.0" y="191.7" text-anchor="middle" font-size="10" font-weight="700" fill="currentColor">180</text>
<text x="272.0" y="134.5" text-anchor="middle" font-size="10" font-weight="700" fill="currentColor">310</text>
<text x="290.0" y="204.9" text-anchor="middle" font-size="10" font-weight="700" fill="currentColor">150</text>
<text x="412.0" y="156.5" text-anchor="middle" font-size="10" font-weight="700" fill="currentColor">260</text>
<text x="430.0" y="165.3" text-anchor="middle" font-size="10" font-weight="700" fill="currentColor">240</text>
  <text x="280.0" y="366" text-anchor="middle" font-size="10" class="chart-muted" fill="#4b5563">Source: Example load test</text>
</svg>
<figcaption>Source: Example load test.</figcaption>
</figure>

## The numbers

The same load test ran for thirty minutes before and after the change, at the same request rate, against the same data set.

| Endpoint | Before (ms) | After (ms) | Note |
|---|---|---|---|
| search | 420 | 180 | most reads hit the cache {hit} |
| catalog | 310 | 150 | warm after the first minute {warm} |
| checkout | 260 | 240 | payment call dominates {external} |

Search gained the most because its queries repeat often. Catalog pages are read far more than they are written, so they stay warm. Checkout spends most of its time waiting on an external payment provider, and a cache in front of the database cannot shorten that wait.

## What to check next

Watch the cache hit rate together with p99. If the hit rate falls, p99 will climb back toward the old numbers. Also watch memory on the cache nodes, because a cache that evicts too early behaves like no cache at all during a traffic spike.

## Sources

- Example load test results, November 2026, run by the author.
