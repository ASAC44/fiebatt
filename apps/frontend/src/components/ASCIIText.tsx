import { useEffect, useRef } from 'react'
import * as THREE from 'three'

const vertexShader = `
varying vec2 vUv;
uniform float uTime;
uniform float uEnableWaves;

void main() {
    vUv = uv;
    float time = uTime * 5.0;

    vec3 transformed = position;
    transformed.x += sin(time + position.y) * 0.5 * uEnableWaves;
    transformed.y += cos(time + position.z) * 0.15 * uEnableWaves;
    transformed.z += sin(time + position.x) * uEnableWaves;

    gl_Position = projectionMatrix * modelViewMatrix * vec4(transformed, 1.0);
}
`

const fragmentShader = `
varying vec2 vUv;
uniform float uTime;
uniform sampler2D uTexture;

void main() {
    float time = uTime;
    vec2 pos = vUv;
    vec2 warped = pos;
    warped.x += sin(time + pos.y * 6.0) * 0.006;
    warped.y += cos(time * 0.8 + pos.x * 5.0) * 0.004;
    vec4 tex = texture2D(uTexture, warped);
    gl_FragColor = vec4(tex.rgb, tex.a);
}
`

const mapRange = (n: number, start: number, stop: number, start2: number, stop2: number) =>
  ((n - start) / (stop - start)) * (stop2 - start2) + start2

class AsciiFilter {
  renderer: THREE.WebGLRenderer
  domElement: HTMLDivElement
  pre: HTMLPreElement
  canvas: HTMLCanvasElement
  context: CanvasRenderingContext2D
  deg: number
  invert: boolean
  fontSize: number
  fontFamily: string
  charset: string
  width: number
  height: number
  cols: number
  rows: number
  center: { x: number; y: number }
  mouse: { x: number; y: number }

  constructor(renderer: THREE.WebGLRenderer, { fontSize, fontFamily, charset, invert }: { fontSize: number; fontFamily: string; charset?: string; invert?: boolean }) {
    this.renderer = renderer
    this.domElement = document.createElement('div')
    this.domElement.style.position = 'absolute'
    this.domElement.style.inset = '0'
    this.domElement.style.width = '100%'
    this.domElement.style.height = '100%'

    this.pre = document.createElement('pre')
    this.domElement.appendChild(this.pre)

    this.canvas = document.createElement('canvas')
    const context = this.canvas.getContext('2d')
    if (!context) {
      throw new Error('2D canvas context unavailable')
    }
    this.context = context
    this.domElement.appendChild(this.canvas)
    this.canvas.style.display = 'none'

    this.deg = 0
    this.invert = invert ?? true
    this.fontSize = fontSize
    this.fontFamily = fontFamily
    this.charset = charset ?? ' .\'`^",:;Il!i~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$'
    this.width = 0
    this.height = 0
    this.cols = 0
    this.rows = 0
    this.center = { x: 0, y: 0 }
    this.mouse = { x: 0, y: 0 }

    this.context.imageSmoothingEnabled = false
    document.addEventListener('mousemove', this.onMouseMove)
  }

  onMouseMove = (e: MouseEvent) => {
    this.mouse = { x: e.clientX, y: e.clientY }
  }

  setSize(width: number, height: number) {
    this.width = width
    this.height = height
    this.renderer.setSize(width, height)
    this.reset()
    this.center = { x: width / 2, y: height / 2 }
    this.mouse = { x: this.center.x, y: this.center.y }
  }

  reset() {
    this.context.font = `${this.fontSize}px ${this.fontFamily}`
    const charWidth = this.context.measureText('A').width || this.fontSize * 0.6
    this.cols = Math.max(1, Math.floor(this.width / (this.fontSize * (charWidth / this.fontSize))))
    this.rows = Math.max(1, Math.floor(this.height / this.fontSize))
    this.canvas.width = this.cols
    this.canvas.height = this.rows

    this.pre.style.fontFamily = this.fontFamily
    this.pre.style.fontSize = `${this.fontSize}px`
    this.pre.style.margin = '0'
    this.pre.style.padding = '0'
    this.pre.style.lineHeight = '1em'
    this.pre.style.position = 'absolute'
    this.pre.style.left = '0'
    this.pre.style.top = '0'
    this.pre.style.zIndex = '2'
    this.pre.style.mixBlendMode = 'screen'
    this.pre.style.backgroundImage = 'linear-gradient(180deg, rgba(255,255,255,0.96) 0%, rgba(225,225,225,0.76) 100%)'
    this.pre.style.webkitBackgroundClip = 'text'
    this.pre.style.webkitTextFillColor = 'transparent'
    this.pre.style.filter = 'drop-shadow(0 0 14px rgba(255,255,255,0.08))'
  }

  render(scene: THREE.Scene, camera: THREE.Camera) {
    this.renderer.render(scene, camera)
    const w = this.canvas.width
    const h = this.canvas.height
    this.context.clearRect(0, 0, w, h)
    this.context.drawImage(this.renderer.domElement, 0, 0, w, h)
    this.asciify(this.context, w, h)
    this.hue()
  }

  hue() {
    const dx = this.mouse.x - this.center.x
    const dy = this.mouse.y - this.center.y
    const deg = (Math.atan2(dy, dx) * 180) / Math.PI
    this.deg += (deg - this.deg) * 0.075
    this.domElement.style.filter = `hue-rotate(${(this.deg * 0.18).toFixed(1)}deg)`
  }

  asciify(ctx: CanvasRenderingContext2D, w: number, h: number) {
    const imgData = ctx.getImageData(0, 0, w, h).data
    let str = ''

    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = x * 4 + y * 4 * w
        const r = imgData[i]
        const g = imgData[i + 1]
        const b = imgData[i + 2]
        const a = imgData[i + 3]

        if (a === 0) {
          str += ' '
          continue
        }

        let gray = (0.3 * r + 0.6 * g + 0.1 * b) / 255
        let idx = Math.floor((1 - gray) * (this.charset.length - 1))
        if (this.invert) idx = this.charset.length - idx - 1
        str += this.charset[Math.max(0, Math.min(this.charset.length - 1, idx))]
      }
      str += '\n'
    }

    this.pre.textContent = str
  }

  dispose() {
    document.removeEventListener('mousemove', this.onMouseMove)
  }
}

class CanvasText {
  canvas: HTMLCanvasElement
  context: CanvasRenderingContext2D
  text: string
  fontSize: number
  fontFamily: string
  color: string

  constructor(text: string, { fontSize, fontFamily, color }: { fontSize: number; fontFamily: string; color: string }) {
    this.canvas = document.createElement('canvas')
    const context = this.canvas.getContext('2d')
    if (!context) {
      throw new Error('2D canvas context unavailable')
    }
    this.context = context
    this.text = text
    this.fontSize = fontSize
    this.fontFamily = fontFamily
    this.color = color
  }

  get font() {
    return `600 ${this.fontSize}px ${this.fontFamily}`
  }

  resize() {
    this.context.font = this.font
    const metrics = this.context.measureText(this.text)
    const textWidth = Math.ceil(metrics.width) + 24
    const textHeight = Math.ceil(metrics.actualBoundingBoxAscent + metrics.actualBoundingBoxDescent) + 24
    this.canvas.width = textWidth
    this.canvas.height = textHeight
  }

  render() {
    this.context.clearRect(0, 0, this.canvas.width, this.canvas.height)
    this.context.fillStyle = this.color
    this.context.font = this.font
    const metrics = this.context.measureText(this.text)
    const yPos = 12 + metrics.actualBoundingBoxAscent
    this.context.fillText(this.text, 12, yPos)
  }
}

class CanvAscii {
  textString: string
  asciiFontSize: number
  textFontSize: number
  textColor: string
  planeBaseHeight: number
  container: HTMLDivElement
  width: number
  height: number
  enableWaves: boolean
  camera: THREE.PerspectiveCamera
  scene: THREE.Scene
  mouse: { x: number; y: number }
  renderer!: THREE.WebGLRenderer
  filter!: AsciiFilter
  textCanvas!: CanvasText
  texture!: THREE.CanvasTexture
  geometry!: THREE.PlaneGeometry
  material!: THREE.ShaderMaterial
  mesh!: THREE.Mesh
  animationFrameId = 0

  constructor(
    { text, asciiFontSize, textFontSize, textColor, planeBaseHeight, enableWaves }: { text: string; asciiFontSize: number; textFontSize: number; textColor: string; planeBaseHeight: number; enableWaves: boolean },
    container: HTMLDivElement,
    width: number,
    height: number
  ) {
    this.textString = text
    this.asciiFontSize = asciiFontSize
    this.textFontSize = textFontSize
    this.textColor = textColor
    this.planeBaseHeight = planeBaseHeight
    this.container = container
    this.width = width
    this.height = height
    this.enableWaves = enableWaves

    this.camera = new THREE.PerspectiveCamera(45, width / height, 1, 1000)
    this.camera.position.z = 30
    this.scene = new THREE.Scene()
    this.mouse = { x: width / 2, y: height / 2 }
  }

  async init() {
    await document.fonts.ready
    this.setMesh()
    this.setRenderer()
  }

  setMesh() {
    this.textCanvas = new CanvasText(this.textString, {
      fontSize: this.textFontSize,
      fontFamily: '"Courier New", monospace',
      color: this.textColor,
    })
    this.textCanvas.resize()
    this.textCanvas.render()

    this.texture = new THREE.CanvasTexture(this.textCanvas.canvas)
    this.texture.minFilter = THREE.NearestFilter
    this.texture.magFilter = THREE.NearestFilter

    const textAspect = this.textCanvas.canvas.width / this.textCanvas.canvas.height
    const planeH = this.planeBaseHeight
    const planeW = planeH * textAspect

    this.geometry = new THREE.PlaneGeometry(planeW, planeH, 36, 36)
    this.material = new THREE.ShaderMaterial({
      vertexShader,
      fragmentShader,
      transparent: true,
      uniforms: {
        uTime: { value: 0 },
        uTexture: { value: this.texture },
        uEnableWaves: { value: this.enableWaves ? 1.0 : 0.0 },
      },
    })

    this.mesh = new THREE.Mesh(this.geometry, this.material)
    this.scene.add(this.mesh)
  }

  setRenderer() {
    this.renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true })
    this.renderer.setPixelRatio(1)
    this.renderer.setClearColor(0x000000, 0)

    this.filter = new AsciiFilter(this.renderer, {
      fontFamily: '"Courier New", monospace',
      fontSize: this.asciiFontSize,
      invert: true,
    })

    this.container.appendChild(this.filter.domElement)
    this.setSize(this.width, this.height)
    this.container.addEventListener('mousemove', this.onMouseMove)
    this.container.addEventListener('touchmove', this.onTouchMove, { passive: true })
  }

  setSize(width: number, height: number) {
    this.width = width
    this.height = height
    this.camera.aspect = width / height
    this.camera.updateProjectionMatrix()
    this.filter.setSize(width, height)
  }

  load() {
    this.animate()
  }

  onMouseMove = (evt: MouseEvent) => {
    const bounds = this.container.getBoundingClientRect()
    this.mouse = { x: evt.clientX - bounds.left, y: evt.clientY - bounds.top }
  }

  onTouchMove = (evt: TouchEvent) => {
    const touch = evt.touches[0]
    if (!touch) return
    const bounds = this.container.getBoundingClientRect()
    this.mouse = { x: touch.clientX - bounds.left, y: touch.clientY - bounds.top }
  }

  animate() {
    const animateFrame = () => {
      this.animationFrameId = requestAnimationFrame(animateFrame)
      this.render()
    }
    animateFrame()
  }

  render() {
    const time = Date.now() * 0.001
    this.textCanvas.render()
    this.texture.needsUpdate = true
    this.mesh.material.uniforms.uTime.value = Math.sin(time * 0.45)
    this.updateRotation()
    this.filter.render(this.scene, this.camera)
  }

  updateRotation() {
    const x = mapRange(this.mouse.y, 0, this.height, 0.35, -0.35)
    const y = mapRange(this.mouse.x, 0, this.width, -0.45, 0.45)
    this.mesh.rotation.x += (x - this.mesh.rotation.x) * 0.05
    this.mesh.rotation.y += (y - this.mesh.rotation.y) * 0.05
  }

  dispose() {
    cancelAnimationFrame(this.animationFrameId)
    this.filter?.dispose()
    if (this.filter?.domElement.parentNode) {
      this.container.removeChild(this.filter.domElement)
    }
    this.container.removeEventListener('mousemove', this.onMouseMove)
    this.container.removeEventListener('touchmove', this.onTouchMove)
    this.scene.traverse((obj) => {
      if (obj instanceof THREE.Mesh) {
        obj.geometry.dispose()
        if (Array.isArray(obj.material)) {
          obj.material.forEach((material) => material.dispose())
        } else {
          obj.material.dispose()
        }
      }
    })
    this.renderer?.dispose()
    this.renderer?.forceContextLoss()
  }
}

export default function ASCIIText({
  text = '< iris >',
  asciiFontSize = 8,
  textFontSize = 200,
  textColor = '#f4f4f4',
  planeBaseHeight = 8,
  enableWaves = true,
}: {
  text?: string
  asciiFontSize?: number
  textFontSize?: number
  textColor?: string
  planeBaseHeight?: number
  enableWaves?: boolean
}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const asciiRef = useRef<CanvAscii | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    let cancelled = false
    let resizeObserver: ResizeObserver | null = null

    const setup = async () => {
      const rect = container.getBoundingClientRect()
      if (rect.width === 0 || rect.height === 0) return

      const instance = new CanvAscii(
        { text, asciiFontSize, textFontSize, textColor, planeBaseHeight, enableWaves },
        container,
        rect.width,
        rect.height
      )

      await instance.init()
      if (cancelled) {
        instance.dispose()
        return
      }

      asciiRef.current = instance
      instance.load()

      resizeObserver = new ResizeObserver((entries) => {
        const entry = entries[0]
        if (!entry || !asciiRef.current) return
        const { width, height } = entry.contentRect
        if (width > 0 && height > 0) {
          asciiRef.current.setSize(width, height)
        }
      })

      resizeObserver.observe(container)
    }

    setup()

    return () => {
      cancelled = true
      resizeObserver?.disconnect()
      asciiRef.current?.dispose()
      asciiRef.current = null
    }
  }, [asciiFontSize, enableWaves, planeBaseHeight, text, textColor, textFontSize])

  return (
    <div
      ref={containerRef}
      style={{
        position: 'absolute',
        inset: 0,
        overflow: 'hidden',
      }}
    />
  )
}
