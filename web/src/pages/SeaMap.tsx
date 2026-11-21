import * as maplibregl from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import type { GeoJSONSource, Map as MLMap, MapGeoJSONFeature } from 'maplibre-gl'
import type * as GeoJSON from 'geojson'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CartesianGrid, Legend as RLegend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Intro, Legend, LoadState, SeasonSwitch, Term, Tour, useSeason, useTour, type TourStep } from '../components/ui'
import { fmt, getJSON, MIN_N, niceDay, niceTime, pct, rowsFor, SEASON_LABEL, useJSON, wavesText, windText, type LogitModel, type Pass, type Summary } from '../lib/data'
import { detectionChance, detectionModel } from '../lib/model'
import { passStoryText, type PassStory } from '../lib/text'

type S2Day = { day: string; time: string; cloud_share: number | null; observed: number; truecolor: string | null; cloudmask: string; bounds: [number, number][]; truecolor_bounds: [number, number][] | null }
type SstDay = { day: string; time: string; image: string | null; valid_share: number; bounds: [number, number][]; min: number; max: number }
type Series = { lons: number[]; lats: number[]; times: string[]; wind: (number | null)[][]; waves: (number | null)[][]; sst_days: string[]; sst: (number | null)[][] }

const LAYERS = [
  { id: 'detections', label: 'Radar detections', group: 'Ships', tip: 'Bright objects at sea found in the radar image', on: true },
  { id: 'ais', label: 'AIS ships at that moment', group: 'Ships', tip: 'Ships that reported their position by AIS, moved to the radar time', on: true },
  { id: 'radar', label: 'Radar image', group: 'Satellite images', tip: 'Sentinel-1 radar backscatter: bright = strong echo (land, ships, rough sea), dark = calm water', on: true },
  { id: 'truecolor', label: 'True-color image', group: 'Satellite images', tip: 'Sentinel-2 optical image from the same day, if one exists (often cloudy)', on: false },
  { id: 'clouds', label: 'Cloud mask', group: 'Satellite images', tip: 'Where Sentinel-2 saw cloud on that day (white)', on: false },
  { id: 'sst', label: 'Sea surface temperature', group: 'Satellite images', tip: 'Sentinel-3 sea surface temperature from the nearest day with a clear view', on: false },
  { id: 'waves', label: 'Wave height', group: 'Weather', tip: 'Significant wave height from the MET Norway wave model at the radar time', on: false },
  { id: 'wind', label: 'Wind arrows', group: 'Weather', tip: 'Wind at 10 m: arrows point where the wind blows to, longer = stronger', on: true },
]

const STEPS: TourStep[] = [
  { target: '#timebar', title: 'Pick a satellite pass', text: 'Slide through the radar passes, or press play to animate them. The label shows the date, time and sea state.' },
  { target: '#layers', title: 'Switch layers', text: 'Turn on images, clouds, temperature, wind and waves. Each switch has a short explanation when you hover over it.' },
  { target: '#ai-switch', title: 'The AI filter', text: 'Turn it on to see which detections the AI judges to be false alarms: they turn into gray rings, and the counter says how many.' },
  { target: '#map', title: 'Click anything', text: 'Click a dot or a ring for its details (blue rings: AIS ships the radar found, pink: ships it missed), or click open sea for a chart of wind, waves and temperature there.' },
  { target: '#interesting', title: 'Not sure where to look?', text: 'Each click jumps to another pass worth a look (the roughest sea, the busiest pass, a rare clear day...) and switches on the layers that tell its story.' },
]

const pick = (on: boolean) => (on ? 'visible' : 'none')

/** True while the window matches a media query (here: laptop widths under 1100 px). */
function useMedia(q: string) {
  const [m, setM] = useState(() => typeof window !== 'undefined' && window.matchMedia(q).matches)
  useEffect(() => {
    const mq = window.matchMedia(q)
    const on = () => setM(mq.matches)
    on()
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [q])
  return m
}

export default function SeaMap() {
  const tour = useTour('map')
  const passesQ = useJSON<Pass[]>('passes.json')
  const [season, setSeason] = useSeason('both')
  const allPasses = passesQ.data
  const passes = useMemo(() => allPasses?.filter((p) => season === 'both' || (p.season ?? 'summer') === season) ?? null, [allPasses, season])
  const { data: summary } = useJSON<Summary>('summary.json')
  const mapRef = useRef<MLMap | null>(null)
  const bboxRef = useRef<number[] | null>(null)
  const divRef = useRef<HTMLDivElement>(null)
  const [ready, setReady] = useState(false)
  const [idx, setIdx] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [layers, setLayers] = useState<Record<string, boolean>>(Object.fromEntries(LAYERS.map((l) => [l.id, l.on])))
  const [aiFilter, setAiFilter] = useState(false)
  const [card, setCard] = useState<Record<string, unknown> | null>(null)
  const [point, setPoint] = useState<{ lon: number; lat: number } | null>(null)
  const [hidden, setHidden] = useState<{ total: number; removed: number; matchedRemoved: number }>({ total: 0, removed: 0, matchedRemoved: 0 })
  const [loadedPass, setLoadedPass] = useState<string | null>(null)
  const [passError, setPassError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const [mapError, setMapError] = useState<string | null>(null)
  const [hint, setHint] = useState<string | null>(null)
  const [passSummary, setPassSummary] = useState<PassStory | null>(null)
  // compact controls on laptop screens: narrower than 1100 px or lower than 820 px
  const narrow = useMedia('(max-width: 1099px), (max-height: 820px)')
  const [layersOpen, setLayersOpen] = useState(false)
  const model = useMemo(() => (summary ? detectionModel(summary) : null), [summary])
  const [params, setParams] = useSearchParams()
  const pass = passes?.[idx]
  const passDrawn = !!pass && loadedPass === pass.id
  // the optical and temperature lists wait until the first radar pass is on screen
  const [extras, setExtras] = useState(false)
  useEffect(() => { if (loadedPass) setExtras(true) }, [loadedPass])
  const { data: s2 } = useJSON<S2Day[]>(extras ? 's2.json' : null)
  const { data: sst } = useJSON<SstDay[]>(extras ? 'sst.json' : null)
  // a new season starts at its first pass, or stays on the pass on screen if it belongs to it
  const changeSeason = (s: typeof season) => {
    const keep = pass && (s === 'both' || (pass.season ?? 'summer') === s) ? pass.id : null
    const next = allPasses?.filter((p) => s === 'both' || (p.season ?? 'summer') === s) ?? []
    setIdx(Math.max(0, keep ? next.findIndex((p) => p.id === keep) : 0))
    setPlaying(false)
    setSeason(s)
  }

  // a pass can be opened from another page with /map?pass=<id>
  useEffect(() => {
    const want = params.get('pass')
    if (!passes || !want) return
    const i = passes.findIndex((p) => p.id === want)
    if (i >= 0) setIdx(i)
    const n = new URLSearchParams(params)
    n.delete('pass')
    setParams(n, { replace: true })
  }, [passes, params, setParams])

  // ---------------------------------------------------------------- map setup
  useEffect(() => {
    if (!divRef.current) return
    maplibregl.setWorkerUrl(workerUrl) // the bundler emits the worker as its own file
    let map: MLMap
    try {
      map = new maplibregl.Map({
      container: divRef.current,
      style: {
        version: 8,
        sources: {},
        layers: [{ id: 'sea', type: 'background', paint: { 'background-color': '#0e2a3d' } }],
      },
      center: [5.0, 60.65], zoom: 7.6, minZoom: 6, maxZoom: 14, attributionControl: false,
      })
    } catch {
      // no WebGL (old browser, or graphics switched off): say so instead of a blank page
      setMapError('This browser cannot draw the interactive map (WebGL is switched off or not supported). The results pages work without it.')
      return
    }
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left')
    map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: 'Coastline © Kartverket · Contains modified Copernicus Sentinel data 2025 and 2026 · MET Norway · Kystverket AIS (NLOD) · Structures © OpenStreetMap contributors' }))
    map.on('load', async () => {
      // the coastline layer exists from the start (other layers are drawn below it); its data follows
      map.addSource('land', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'land', type: 'fill', source: 'land', paint: { 'fill-color': '#e7e2d6', 'fill-opacity': 1 } })
      map.addLayer({ id: 'land-line', type: 'line', source: 'land', paint: { 'line-color': '#b9b09c', 'line-width': 0.5 } })
      // arrow icon for wind
      const c = document.createElement('canvas'); c.width = 32; c.height = 32
      const g = c.getContext('2d')!
      g.strokeStyle = '#ffffff'; g.fillStyle = '#ffffff'; g.lineWidth = 3
      g.beginPath(); g.moveTo(16, 30); g.lineTo(16, 8); g.stroke()
      g.beginPath(); g.moveTo(16, 2); g.lineTo(9, 13); g.lineTo(23, 13); g.closePath(); g.fill()
      map.addImage('arrow', { width: 32, height: 32, data: new Uint8Array(g.getImageData(0, 0, 32, 32).data) })
      setReady(true)
      loadLand(map)
      // the credit line starts folded (an "i" button), so it does not cover the map on small screens
      map.getContainer().querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show')
    })
    mapRef.current = map
    return () => map.remove()
  }, [])

  const loadLand = (map: MLMap) => {
    getJSON<GeoJSON.FeatureCollection>('land.geojson')
      .then((land) => { (map.getSource('land') as GeoJSONSource | undefined)?.setData(land); setPassError((e) => (e === 'land' ? null : e)) })
      .catch(() => setPassError('land'))
  }

  // ---------------------------------------------------------------- shade everything outside the study area
  useEffect(() => {
    const map = mapRef.current
    if (summary) bboxRef.current = summary.area.bbox
    if (!map || !ready || !summary || map.getSource('outside')) return
    const [w, s, e, n] = summary.area.bbox
    const box: [number, number][] = [[w, s], [e, s], [e, n], [w, n], [w, s]]
    map.addSource('outside', {
      type: 'geojson',
      data: { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[[-30, 40], [40, 40], [40, 80], [-30, 80], [-30, 40]], box] } },
    })
    map.addLayer({ id: 'outside', type: 'fill', source: 'outside', paint: { 'fill-color': '#0e2a3d', 'fill-opacity': 0.82 } })
    map.addLayer({ id: 'outside-edge', type: 'line', source: 'outside', paint: { 'line-color': '#9fb3c3', 'line-width': 1, 'line-dasharray': [3, 2] } })
  }, [ready, summary])

  // ---------------------------------------------------------------- per-pass layers
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready || !pass) return
    let cancelled = false
    const setImage = (id: string, url: string | null, coords: [number, number][] | null, opacity: number, before?: string) => {
      const has = map.getSource(id)
      if (!url || !coords) {
        if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'none')
        return
      }
      const coordinates = coords as [[number, number], [number, number], [number, number], [number, number]]
      if (has) (has as maplibregl.ImageSource).updateImage({ url: `/data/${url}`, coordinates })
      else {
        map.addSource(id, { type: 'image', url: `/data/${url}`, coordinates })
        map.addLayer({ id, type: 'raster', source: id, paint: { 'raster-opacity': opacity, 'raster-fade-duration': 0 } }, before)
      }
    }
    // the light radar image first; the full one replaces it once this pass is on screen
    setImage('radar', pass.image_lite ?? pass.image, pass.bounds, 0.95, 'land')
    setImage('waves', pass.waves_image, pass.wave_bounds, 0.6, 'land')
    ;(async () => {
      setPassError((e) => (e === 'land' ? e : null))
      let det: GeoJSON.FeatureCollection, ais: GeoJSON.FeatureCollection, wind: GeoJSON.FeatureCollection
      try {
        [det, ais, wind] = await Promise.all([
          getJSON<GeoJSON.FeatureCollection>(`detections/${pass.id}.geojson`),
          getJSON<GeoJSON.FeatureCollection>(`ais/${pass.id}.geojson`),
          getJSON<GeoJSON.FeatureCollection>(pass.wind),
        ])
      } catch {
        if (!cancelled) { setPassError(pass.id); setPlaying(false) }
        return
      }
      if (cancelled) return
      const put = (id: string, data: GeoJSON.FeatureCollection, add: () => void) => {
        const src = map.getSource(id) as GeoJSONSource | undefined
        if (src) src.setData(data)
        else { map.addSource(id, { type: 'geojson', data }); add() }
      }
      put('wind', wind, () => map.addLayer({
        id: 'wind', type: 'symbol', source: 'wind',
        layout: {
          'icon-image': 'arrow', 'icon-rotate': ['+', ['get', 'dir'], 180], 'icon-rotation-alignment': 'map',
          'icon-size': ['interpolate', ['linear'], ['get', 'speed'], 0, 0.35, 20, 1.0], 'icon-allow-overlap': true,
        },
        paint: { 'icon-opacity': 0.75 },
      }))
      put('ais', ais, () => {
        // a white halo under each ring keeps it visible on top of the wave colors
        map.addLayer({
          id: 'ais-halo', type: 'circle', source: 'ais',
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 5.5, 12, 10.5], 'circle-color': 'rgba(0,0,0,0)',
            'circle-stroke-color': '#ffffff', 'circle-stroke-width': 4, 'circle-stroke-opacity': 0.85,
          },
        })
        map.addLayer({
          id: 'ais', type: 'circle', source: 'ais',
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 5.5, 12, 10.5], 'circle-color': 'rgba(0,0,0,0)',
            'circle-stroke-color': ['case', ['get', 'detected'], '#4c6ef5', '#e64980'], 'circle-stroke-width': 2,
          },
        })
      })
      put('detections', det, () => {
        map.addLayer({
          id: 'detections-hidden', type: 'circle', source: 'detections', filter: ['<', ['get', 'ai'], 0.5],
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 3, 12, 7], 'circle-color': 'rgba(0,0,0,0)',
            'circle-stroke-color': '#868e96', 'circle-stroke-width': 1.5, 'circle-opacity': 0.8,
          },
        })
        map.addLayer({
          id: 'detections', type: 'circle', source: 'detections',
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 3.5, 12, 7.5],
            'circle-color': ['case', ['get', 'matched'], '#10a37f', '#f0803c'],
            'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.2,
          },
        })
      })
      const feats = det.features
      const removed = feats.filter((f) => (f.properties?.ai ?? 1) < 0.5)
      setHidden({ total: feats.length, removed: removed.length, matchedRemoved: removed.filter((f) => f.properties?.matched).length })
      setPassSummary(passStory(det, ais))
      setLoadedPass(pass.id)
      applyVisibility()
      if (pass.image_lite) {
        const full = new Image()
        full.onload = () => {
          if (cancelled) return
          const coordinates = pass.bounds as [[number, number], [number, number], [number, number], [number, number]]
          ;(map.getSource('radar') as maplibregl.ImageSource | undefined)?.updateImage({ url: `/data/${pass.image}`, coordinates })
        }
        full.src = `/data/${pass.image}`
      }
      // only once this pass is drawn: fetch the next two ahead of time, so play and "next" feel instant
      const at = passes?.indexOf(pass) ?? -1
      for (const k of [1, 2]) {
        const nxt = passes && at >= 0 ? passes[(at + k) % passes.length] : null
        if (!nxt || nxt === pass) continue
        for (const f of [`detections/${nxt.id}.geojson`, `ais/${nxt.id}.geojson`, nxt.wind]) getJSON(f).catch(() => undefined)
        const img = new Image(); img.src = `/data/${nxt.image_lite ?? nxt.image}`
      }
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, pass, reload])

  // optical image, cloud mask and sea temperature: drawn under the radar once their lists are in
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready || !pass || !s2 || !sst) return
    const setImage = (id: string, url: string | null, coords: [number, number][] | null, opacity: number, before?: string) => {
      const has = map.getSource(id)
      if (!url || !coords) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'none'); return }
      const coordinates = coords as [[number, number], [number, number], [number, number], [number, number]]
      if (has) (has as maplibregl.ImageSource).updateImage({ url: `/data/${url}`, coordinates })
      else {
        map.addSource(id, { type: 'image', url: `/data/${url}`, coordinates })
        map.addLayer({ id, type: 'raster', source: id, paint: { 'raster-opacity': opacity, 'raster-fade-duration': 0 } }, before)
      }
    }
    const s2day = s2.find((d) => d.day === pass.s2_day) ?? null
    const sstday = sst.find((d) => d.day === pass.sst_day) ?? null
    const below = map.getLayer('radar') ? 'radar' : 'land'
    setImage('sst', sstday?.image ?? null, sstday?.bounds ?? null, 0.9, below)
    setImage('truecolor', s2day?.truecolor ?? null, s2day?.truecolor_bounds ?? null, 1, below)
    setImage('clouds', s2day?.cloudmask ?? null, s2day?.bounds ?? null, 0.85, map.getLayer('wind') ? 'wind' : undefined)
    applyVisibility()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, pass, s2, sst])

  const applyVisibility = () => {
    const map = mapRef.current
    if (!map) return
    for (const l of LAYERS) if (map.getLayer(l.id)) map.setLayoutProperty(l.id, 'visibility', pick(layers[l.id]))
    if (map.getLayer('ais-halo')) map.setLayoutProperty('ais-halo', 'visibility', pick(layers.ais))
    // with the AI filter on, the detections it rejects stay visible as faint gray rings
    if (map.getLayer('detections-hidden')) map.setLayoutProperty('detections-hidden', 'visibility', pick(layers.detections && aiFilter))
    if (map.getLayer('detections')) map.setFilter('detections', aiFilter ? ['>=', ['get', 'ai'], 0.5] : null)
  }
  useEffect(applyVisibility)

  // ---------------------------------------------------------------- clicks
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const onClick = (e: maplibregl.MapMouseEvent) => {
      // a few pixels of slack around the click, so small dots are easy to hit with a finger
      const r = 7
      const box: [maplibregl.PointLike, maplibregl.PointLike] = [[e.point.x - r, e.point.y - r], [e.point.x + r, e.point.y + r]]
      const feats = map.queryRenderedFeatures(box, { layers: ['detections', 'detections-hidden', 'ais'].filter((l) => map.getLayer(l)) }) as MapGeoJSONFeature[]
      if (feats.length) {
        // the feature closest to the click wins; detections before AIS rings at equal distance
        const d = (f: MapGeoJSONFeature) => {
          const c = (f.geometry as GeoJSON.Point).coordinates
          const q = map.project([c[0], c[1]])
          return Math.hypot(q.x - e.point.x, q.y - e.point.y) + (f.layer.id === 'ais' ? 0.5 : 0)
        }
        const best = feats.reduce((a, b) => (d(b) < d(a) ? b : a))
        setPoint(null)
        setHint(null)
        setCard({ kind: best.layer.id, ...best.properties })
        return
      }
      setCard(null)
      const onLand = map.queryRenderedFeatures(e.point, { layers: ['land'] }).length > 0
      const [w, s, east, n] = bboxRef.current ?? [-180, -90, 180, 90]
      const inside = e.lngLat.lng >= w && e.lngLat.lng <= east && e.lngLat.lat >= s && e.lngLat.lat <= n
      if (onLand || !inside) {
        setPoint(null)
        setHint(onLand ? 'That is land. Click open sea for a wind, wave and temperature chart, or click a dot or ring for details.'
          : 'That is outside the study area (the shaded part). Click inside the box.')
        return
      }
      setHint(null)
      setPoint({ lon: e.lngLat.lng, lat: e.lngLat.lat })
    }
    const enter = () => { map.getCanvas().style.cursor = 'pointer' }
    const leave = () => { map.getCanvas().style.cursor = '' }
    map.on('click', onClick)
    for (const l of ['detections', 'detections-hidden', 'ais']) { map.on('mouseenter', l, enter); map.on('mouseleave', l, leave) }
    return () => { map.off('click', onClick) }
  }, [ready])

  // ---------------------------------------------------------------- play: the next pass only after this one has loaded
  useEffect(() => {
    if (!playing || !passes || !pass || loadedPass !== pass.id) return
    const t = setTimeout(() => setIdx((i) => (i + 1) % passes.length), 2500)
    return () => clearTimeout(t)
  }, [playing, passes, pass, loadedPass])

  // the hint after a click on land fades by itself
  useEffect(() => {
    if (!hint) return
    const t = setTimeout(() => setHint(null), 5000)
    return () => clearTimeout(t)
  }, [hint])

  // a short guided story: each click jumps to another pass worth a look, with the layers that tell it
  const stories = useMemo(() => (passes ? buildStories(passes, s2 ?? []) : []), [passes, s2])
  const [storyAt, setStoryAt] = useState(-1)
  const interesting = () => {
    if (!passes || !stories.length) return
    const k = (storyAt + 1) % stories.length
    const st = stories[k]
    setStoryAt(k)
    const i = passes.findIndex((p) => p.id === st.pass)
    if (i >= 0) setIdx(i)
    setPlaying(false)
    setAiFilter(st.ai)
    setCard(null)
    setPoint(null)
    setLayers((l) => ({ ...l, radar: !st.layers.truecolor, detections: true, ais: true, waves: false, wind: false, truecolor: false, clouds: false, sst: false, ...st.layers }))
    mapRef.current?.flyTo({ center: [4.85, 60.68], zoom: 8.1 })
  }
  const story = storyAt >= 0 && pass && stories[storyAt]?.pass === pass.id ? stories[storyAt] : null

  return (
    <main className="page wide">
      <Intro
        what={<>A map of the sea off Bergen with every radar pass of the study, winter and summer: the bright spots the radar found, the ships that reported their position by AIS, and the clouds, temperature, wind and waves at that moment.</>}
        how={<>Move the time slider or press play. Switch layers on the left. Click a dot or ring for details, or open sea for a weather chart.</>}
        notice={<>Pink rings are AIS ships the radar missed, mostly small ones. Orange dots have no AIS ship nearby: a boat without AIS, or a false alarm. Switch on the AI filter to see which ones the AI rejects.</>}
      />
      <div className={`map-wrap ${narrow ? 'narrow' : ''}`}>
        <div className="map" id="map" ref={divRef} />
        {mapError ? <div className="map-loading map-error" role="alert">{mapError}</div>
          : passError ? (
            <div className="map-loading map-error" role="alert">
              {passError === 'land' ? "Couldn't load the coastline." : "Couldn't load this radar pass."} The connection may have dropped.
              <button className="btn primary" style={{ marginLeft: '0.6rem' }}
                onClick={() => { if (passError === 'land' && mapRef.current) loadLand(mapRef.current); else setReload((r) => r + 1) }}>Try again</button>
            </div>
          )
          : (!ready || !pass || loadedPass !== pass.id) && !passesQ.error
            ? <div className="map-loading"><span className="spinner" aria-hidden="true" />Loading the {ready && pass ? 'radar pass' : 'map'}…{passesQ.slow && !pass ? ' The free server is waking up (20 to 30 s, once).' : ''}</div>
            : null}
        {hint && <div className="map-hint" role="status">{hint}</div>}

        <div className={`panel layers ${narrow && !layersOpen ? 'collapsed' : ''}`} id="layers">
          <SeasonSwitch value={season} onChange={changeSeason} />
          {narrow && (
            <button className="btn layers-btn" aria-expanded={layersOpen} onClick={() => setLayersOpen(!layersOpen)}>
              {layersOpen ? 'Hide layers ▴' : 'Layers ▾'}
            </button>
          )}
          {(!narrow || layersOpen) && ['Ships', 'Satellite images', 'Weather'].map((g) => (
            <div key={g}>
              <div className="group">{g}</div>
              {LAYERS.filter((l) => l.group === g).map((l) => (
                <div key={l.id}>
                  <label data-tip={l.tip}>
                    <input type="checkbox" aria-label={l.label} checked={layers[l.id]} onChange={(e) => setLayers({ ...layers, [l.id]: e.target.checked })} />
                    {l.label}
                  </label>
                  {l.id === 'detections' && layers.detections && (
                    <Legend items={[
                      { color: 'var(--matched)', label: 'AIS ship nearby', tip: 'A radar detection with an AIS ship within 400 m' },
                      { color: 'var(--unmatched)', label: 'no AIS ship nearby', tip: 'A boat without AIS, a structure or a false alarm' },
                      ...(aiFilter ? [{ color: '#868e96', label: 'rejected by the AI', ring: true, tip: 'The AI gives it under 50% chance of being a ship' }] : []),
                    ]} />
                  )}
                  {l.id === 'ais' && layers.ais && (
                    <Legend items={[
                      { color: 'var(--ais)', label: 'found by radar', ring: true, tip: 'A radar detection lies within 400 m of this AIS position' },
                      { color: 'var(--missed)', label: 'missed by radar', ring: true, tip: 'No radar detection within 400 m' },
                    ]} />
                  )}
                  {l.id === 'waves' && layers.waves && <WaveScale />}
                </div>
              ))}
            </div>
          ))}
        </div>

        <div className="right-col">
          <div className={`panel counter ${narrow ? 'compact' : ''}`} id="ai-switch">
            <label className="row" style={{ cursor: 'pointer' }} data-tip="Hide the detections that the AI model calls false alarms (ship probability below 50%)">
              <span className="switch"><input type="checkbox" aria-label="AI filter" checked={aiFilter} onChange={(e) => setAiFilter(e.target.checked)} /><span /></span>
              <b>AI filter</b>
            </label>
            <div className="small" style={{ marginTop: '0.35rem' }}>
              {!passDrawn ? <span className="muted">Loading this pass…</span> : aiFilter
                ? <>The AI rejects <b style={{ color: 'var(--ai)' }}>{hidden.removed}</b> of {hidden.total} detections as likely false alarms (gray rings){hidden.matchedRemoved ? <>; {hidden.matchedRemoved} of them had an AIS ship nearby</> : null}.</>
                : <>{hidden.total} detections on this pass. The AI would reject <b>{hidden.removed}</b>.</>}
            </div>
            {passSummary && passDrawn && (narrow
              ? <details className="small story"><summary>What happened in this pass</summary>{passStoryText(passSummary)}</details>
              : <div className="small story" aria-live="polite">{passStoryText(passSummary)}</div>
            )}
            {story && (narrow
              ? <details className="small note-box" open><summary><b>{story.title}</b></summary>{story.text}</details>
              : <div className="small note-box"><b>{story.title}.</b> {story.text}</div>)}
            <button className="btn" id="interesting" style={{ marginTop: '0.5rem', width: '100%', justifyContent: 'center' }} onClick={interesting}
              data-tip="Jump to a pass worth a look, with the right layers switched on. Click again for the next one.">
              {storyAt < 0 ? 'Show me something interesting' : `Show me another (${storyAt + 1} of ${stories.length})`}
            </button>
          </div>
          {!narrow && card && <DetailCard card={card} summary={summary} season={pass?.season ?? 'summer'} model={model} onClose={() => setCard(null)} />}
          {!narrow && point && <PointChart point={point} passTime={pass?.time ?? null} season={pass?.season ?? 'summer'} onClose={() => setPoint(null)} />}
        </div>
        {/* under 1100 px the card opens as a sheet over the lower part of the map, wide enough to need no inner scrolling */}
        {narrow && (card || point) && (
          <div className="sheet" role="dialog" aria-label="Details">
            {card && <DetailCard card={card} summary={summary} season={pass?.season ?? 'summer'} model={model} onClose={() => setCard(null)} />}
            {point && <PointChart point={point} passTime={pass?.time ?? null} season={pass?.season ?? 'summer'} onClose={() => setPoint(null)} />}
          </div>
        )}

        <div className="panel timebar" id="timebar">
          <button className="btn" onClick={() => setPlaying(!playing)} data-tip="Play through all passes, one every 2.5 seconds" aria-label={playing ? 'Pause' : 'Play'}>
            {playing ? '❚❚ Pause' : '▶ Play'}
          </button>
          <button className="btn" disabled={!passes || idx === 0} onClick={() => { setIdx(idx - 1); setPlaying(false) }} data-tip="Previous pass" aria-label="Previous pass">‹</button>
          <input type="range" min={0} max={(passes?.length ?? 1) - 1} value={idx} onChange={(e) => { setIdx(+e.target.value); setPlaying(false) }}
            data-tip="Drag to choose a satellite pass" aria-label="Satellite pass" />
          <button className="btn" disabled={!passes || idx === passes.length - 1} onClick={() => { setIdx(idx + 1); setPlaying(false) }} data-tip="Next pass" aria-label="Next pass">›</button>
          <div className="pass-label">
            {pass ? <>
              {niceTime(pass.time)} <span className="muted small">· {season === 'both' ? `${SEASON_LABEL[pass.season ?? 'summer'].toLowerCase()} · ` : ''}pass {idx + 1} of {passes!.length}</span>
              <div className="small muted" data-tip="Median over open sea (wave-model cells at least 3 km from land) at the pass time">{pass.sea_waves != null ? <>open sea: wind {fmt(pass.sea_wind)} m/s · waves {fmt(pass.sea_waves)} m (max {fmt(pass.sea_waves_max)} m)</> : <>no wave data for this pass</>} · radar found {pass.ais_detected} of {pass.ais_visible} AIS ships</div>
            </> : passesQ.error
              ? <>Couldn't load the list of passes. <button className="btn" onClick={passesQ.retry}>Try again</button></>
              : 'Loading passes…'}
          </div>
        </div>
      </div>
      <Tour id="map" steps={STEPS} open={tour.open} onClose={tour.close} />
    </main>
  )
}

function WaveScale() {
  return (
    <div className="tiny muted" style={{ margin: '0.1rem 0 0.3rem 1.6rem' }} data-tip="Color scale for the wave height layer">
      <div style={{ height: 8, borderRadius: 4, background: 'linear-gradient(90deg,#d0f0ff,#5fb3e8,#2f6fbf,#6a3d9a,#c2185b)' }} />
      <div className="row" style={{ justifyContent: 'space-between' }}><span>0 m</span><span>2 m</span><span>4 m+</span></div>
    </div>
  )
}

function DetailCard({ card, summary, season, model, onClose }: { card: Record<string, unknown>; summary: Summary | null; season: 'summer' | 'winter'; model: LogitModel | null; onClose: () => void }) {
  const byLength = (summary && rowsFor(summary.by_length, season)) ?? []
  const winter = season === 'winter' ? 1 : 0
  const n = (k: string, d = 1) => (typeof card[k] === 'number' ? fmt(card[k] as number, d) : 'n/a')
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => { ref.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [card])
  if (card.kind === 'ais') {
    return (
      <div className="panel info-card" ref={ref}>
        <button className="close-x" onClick={onClose} aria-label="Close">×</button>
        <h3><span className="sw ring" style={{ borderColor: card.detected ? 'var(--ais)' : 'var(--missed)' }} /> AIS ship, {card.detected ? 'found by radar' : 'missed by radar'}</h3>
        <dl>
          <dt>Type</dt><dd>{String(card.type ?? 'unknown')}</dd>
          <dt>Length class</dt><dd>{String(card.length_class ?? 'unknown')}</dd>
          <dt>Speed</dt><dd>{n('sog')} knots</dd>
          <dt>Seen by radar</dt><dd>{card.detected ? 'yes' : 'no'}</dd>
          <dt>Wind there</dt><dd>{windText(card.wind)}</dd>
          <dt>Waves there</dt><dd>{wavesText(card.waves)}</dd>
        </dl>
        {(() => {
          const cls = String(card.length_class ?? '').replace(/^under /, '0-')
          const r = byLength.find((x) => x.cls === cls)
          // the model's chance for a ship of this size class in the wind and waves at this very spot
          const mid: Record<string, number> = { '0-30 m': 18, '30-60 m': 45, '60-100 m': 80, '100-200 m': 140, 'over 200 m': 250 }
          const w = typeof card.wind === 'number' ? card.wind : null
          const h = typeof card.waves === 'number' ? card.waves : null
          const expect = model && mid[cls] && w != null && h != null ? detectionChance(model, mid[cls], w, h, winter) : null
          const verdict = expect == null ? null
            : card.detected ? (expect < 0.4 ? 'A good catch: the odds were against it.' : 'As expected.')
              : (expect > 0.75 ? 'A surprising miss: most ships like this are found here.' : 'Not surprising for a ship this size in these conditions.')
          return (
            <>
              {r && r.n >= MIN_N && <p className="small" style={{ marginBottom: 0 }}>Across the {season} test passes the radar found {pct(r.rate)} of ships {cls} long.</p>}
              {expect != null && <p className="small">For a ship this size in this spot's wind and waves, the model expects a {pct(expect)} chance of detection. {verdict}</p>}
            </>
          )
        })()}
        <p className="tiny muted">Identity is never shown: each ship is only a type and a length class.</p>
      </div>
    )
  }
  const matched = Boolean(card.matched)
  const ai = typeof card.ai === 'number' ? (card.ai as number) : null
  return (
    <div className="panel info-card" ref={ref}>
      <button className="close-x" onClick={onClose} aria-label="Close">×</button>
      <h3><span className="sw" style={{ background: matched ? 'var(--matched)' : 'var(--unmatched)' }} /> Radar detection{card.line ? ', line-shaped' : ''}{ai != null && ai < 0.5 ? ', rejected by the AI' : ''}</h3>
      <dl>
        <dt><Term k="probability">AI: ship probability</Term></dt><dd style={{ color: 'var(--ai)' }}>{ai == null ? 'n/a' : pct(ai)}</dd>
        <dt>AI: estimated length</dt><dd>{typeof card.ai_len === 'number' ? `${n('ai_len', 0)} m` : 'n/a'}</dd>
        <dt><Term k="db">Radar brightness</Term></dt><dd>{n('peak_db')} dB</dd>
        <dt><Term k="contrast">Contrast to the sea</Term></dt><dd>{n('scr_db')} dB</dd>
        <dt>Size in the image</dt><dd>{typeof card.len === 'number' ? `${n('len', 0)} m` : 'n/a'}</dd>
        <dt>Matched AIS ship</dt><dd>{matched ? `${card.type ?? 'unknown type'}, ${card.length_class ?? 'unknown length'}` : 'none within 400 m'}</dd>
        {matched && <><dt>Distance to AIS</dt><dd>{n('dist', 0)} m</dd></>}
        <dt>Wind there</dt><dd>{windText(card.wind)}</dd>
        <dt>Waves there</dt><dd>{wavesText(card.waves)}</dd>
      </dl>
      {Boolean(card.line) && <p className="small note-box">Line-shaped: a long, thin bright echo. More likely a bridge, cable or other structure than a boat; it stays in the data with this label.</p>}
      <p className="tiny muted">{matched ? 'A ship that reported its position: the radar saw it.' : 'No AIS ship nearby: a boat without AIS, a fixed object, or a bright patch of sea.'}</p>
    </div>
  )
}

function PointChart({ point, passTime, season, onClose }: { point: { lon: number; lat: number }; passTime: string | null; season: 'summer' | 'winter'; onClose: () => void }) {
  const seriesQ = useJSON<Partial<Record<'summer' | 'winter', Series>>>('grid_series.json')
  const data = seriesQ.data?.[season] ?? null
  const rows = useMemo(() => {
    if (!data) return null
    const j = data.lons.reduce((b, v, i) => (Math.abs(v - point.lon) < Math.abs(data.lons[b] - point.lon) ? i : b), 0)
    const i = data.lats.reduce((b, v, k) => (Math.abs(v - point.lat) < Math.abs(data.lats[b] - point.lat) ? k : b), 0)
    const k = i * data.lons.length + j
    const sstByDay = new Map(data.sst_days.map((d, q) => [d, data.sst[k]?.[q] ?? null]))
    const out = data.times.map((t, q) => ({ t, day: t.slice(0, 10), wind: data.wind[k]?.[q] ?? null, waves: data.waves[k]?.[q] ?? null, sst: null as number | null }))
    for (const r of out) if (r.t.endsWith('12:00')) r.sst = sstByDay.get(r.day) ?? null
    // the model time step closest to the radar pass on screen, marked in the chart
    const at = passTime ? Date.parse(passTime) : NaN
    const now = Number.isNaN(at) ? null : out.reduce((b, r) => (Math.abs(Date.parse(r.t + 'Z') - at) < Math.abs(Date.parse(b.t + 'Z') - at) ? r : b), out[0])?.t ?? null
    return { out, now, valid: out.some((r) => r.waves != null) }
  }, [data, point, passTime])
  return (
    <div className="panel info-card wide-card">
      <button className="close-x" onClick={onClose} aria-label="Close">×</button>
      <h3>Weather at {point.lat.toFixed(2)}° N, {point.lon.toFixed(2)}° E</h3>
      {!rows ? <LoadState qs={[seriesQ]} what="weather series" /> : !rows.valid ? <p className="small muted">No model data here (too close to land). Try a spot further out at sea.</p> : (
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={rows.out} margin={{ left: -18, right: 4, top: 6 }}>
            <CartesianGrid stroke="#eef1f4" />
            <XAxis dataKey="t" tickFormatter={(t: string) => niceDay(t)} minTickGap={40} fontSize={11} />
            <YAxis yAxisId="a" fontSize={11} />
            <YAxis yAxisId="b" orientation="right" fontSize={11} domain={['auto', 'auto']} />
            <Tooltip labelFormatter={(t) => niceTime(String(t))} formatter={(v, name) => [fmt(Number(v)), String(name)]} />
            <RLegend wrapperStyle={{ fontSize: 11 }} />
            {rows.now && <ReferenceLine yAxisId="a" x={rows.now} stroke="#17212b" strokeDasharray="3 3" label={{ value: 'this pass', position: 'insideTopLeft', fontSize: 10, fill: '#17212b' }} />}
            <Line yAxisId="a" dataKey="wind" name="wind (m/s)" stroke="#1c7ed6" dot={false} strokeWidth={1.4} />
            <Line yAxisId="a" dataKey="waves" name="waves (m)" stroke="#7c4dff" dot={false} strokeWidth={1.4} />
            <Line yAxisId="b" dataKey="sst" name="sea temp. (°C)" stroke="#e8590c" dot={{ r: 3 }} connectNulls={false} strokeWidth={0} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      )}
      <p className="tiny muted">The dashed line marks the radar pass on the map. Wind and waves: MET Norway wave model, every 3 hours. Sea temperature: Sentinel-3 on days with a clear view (summer only).</p>
    </div>
  )
}

/** Counts for the plain-language summary of one pass. */
function passStory(det: GeoJSON.FeatureCollection, ais: GeoJSON.FeatureCollection): PassStory {
  const a = ais.features.map((f) => f.properties ?? {})
  const d = det.features.map((f) => f.properties ?? {})
  const missed = a.filter((p) => !p.detected)
  const small = (lc: unknown) => lc === 'under 30 m' || lc === '30-60 m'
  const big = (lc: unknown) => lc === '100-200 m' || lc === 'over 200 m'
  const noAis = d.filter((p) => !p.matched)
  return {
    ais: a.length, found: a.length - missed.length, missed: missed.length,
    missedSmall: missed.filter((p) => small(p.length_class)).length, missedBig: missed.filter((p) => big(p.length_class)).length,
    dets: d.length, noAis: noAis.length, noAisRejected: noAis.filter((p) => typeof p.ai === 'number' && p.ai < 0.5).length,
  }
}

type Story = { pass: string; title: string; text: string; ai: boolean; layers: Record<string, boolean> }

/** Passes worth a look, each with a one-line reason and the layers that show it (from the pass list only). */
function buildStories(passes: Pass[], s2: S2Day[]): Story[] {
  const by = (f: (p: Pass) => number) => [...passes].sort((a, b) => f(b) - f(a))[0]
  const out: Story[] = []
  const add = (p: Pass | undefined, st: Omit<Story, 'pass'>) => { if (p && !out.some((o) => o.pass === p.id)) out.push({ pass: p.id, ...st }) }
  const rough = by((p) => p.sea_waves ?? 0)
  // the storm is shown on the radar image itself; the wave layer would cover it, so it stays off
  add(rough, { title: 'Roughest sea', ai: false, layers: { wind: true },
    text: `Waves of ${fmt(rough?.sea_waves)} m over open sea (up to ${fmt(rough?.sea_waves_max)} m), wind ${fmt(rough?.sea_wind)} m/s: the bright, grainy texture of the sea is the storm in the radar image. The radar found ${rough?.ais_detected} of ${rough?.ais_visible} AIS ships; click the pink rings to see which ones it missed. Switch on "Wave height" for the wave model's map.` })
  const hardest = [...passes].filter((p) => p.ai_error_share != null).sort((a, b) => (b.ai_error_share ?? 0) - (a.ai_error_share ?? 0))[0]
  add(hardest, { title: 'Where the AI struggles most', ai: true, layers: {},
    text: `On this pass the AI disagreed with AIS on ${pct(hardest?.ai_error_share ?? 0)} of the labeled detections (${hardest?.ai_errors ?? 0}), its highest share of any pass. The AI filter is on: compare the gray rings with the AIS rings.` })
  const busy = by((p) => p.ais_visible)
  add(busy, { title: 'Busiest pass', ai: false, layers: {},
    text: `${busy?.ais_visible} ships reported by AIS at the moment of the pass, and the radar found ${busy?.ais_detected}. Pink rings are the ones it missed; click a few to see their size.` })
  const dark = by((p) => p.unmatched)
  add(dark, { title: 'Most bright spots without AIS', ai: true, layers: {},
    text: `${dark?.unmatched} detections had no AIS ship nearby. The AI filter is on: gray rings are spots it calls false alarms; orange dots it keeps may be boats that do not report.` })
  const clear = [...s2].filter((d) => d.truecolor && d.cloud_share != null).sort((a, b) => (a.cloud_share ?? 1) - (b.cloud_share ?? 1))
    .map((d) => passes.find((p) => p.s2_day === d.day)).find(Boolean)
  add(clear, { title: 'A rare clear day', ai: false, layers: { truecolor: true },
    text: 'The optical satellite saw the sea on this day too: the photo is on, the radar image is off. Switch between them to compare what each one shows.' })
  const calm = [...passes].filter((p) => p.sea_waves != null).sort((a, b) => (a.sea_waves ?? 0) - (b.sea_waves ?? 0))[0]
  add(calm, { title: 'Calmest sea', ai: false, layers: {},
    text: `Waves around ${fmt(calm?.sea_waves)} m over open sea. Calm water reflects the radar away and looks dark, so ships stand out; here the radar found ${calm?.ais_detected} of ${calm?.ais_visible} AIS ships.` })
  return out
}
