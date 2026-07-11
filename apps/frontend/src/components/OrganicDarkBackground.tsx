import { useEffect, useRef } from 'react'

const vertexShader = `#version 300 es
precision highp float;

in vec2 a_position;

void main() {
  gl_Position = vec4(a_position, 0.0, 1.0);
}`

const fragmentShader = `#version 300 es
precision highp float;

out vec4 outColor;

uniform float iTime;
uniform vec2 iResolution;

const float uSpeed = 0.6;
const float uScaleX = 2.0;
const float uScaleY = 2.0;
const float uColorOffset = 3.0;
const float uIterLimit = 10.0;
const float uRoundness = 1.0;
const float uZoom = 0.74;

const vec3 uColor1 = vec3(0.369, 0.369, 0.369);
const vec3 uColor2 = vec3(0.749, 0.749, 0.749);
const vec3 uColor3 = vec3(0.851, 0.851, 0.851);

float random(vec2 st) {
  return fract(sin(dot(st.xy, vec2(12.9898, 78.233))) * 43758.5453123);
}

void mainImage(out vec4 O, vec2 I) {
  float i = 0.0;
  float z = 0.0;
  float d = 0.0;
  O = vec4(0.0);

  for (O *= i; i++ < uIterLimit;) {
    vec2 centered = (I + I - iResolution.xy) * uZoom;
    vec3 p = z * normalize(vec3(centered, 0.0) - vec3(iResolution.x, iResolution.y, iResolution.y));
    vec3 v;

    p.x += sin(p.x + iTime * uSpeed * 0.5) + cos(p.y + iTime * uSpeed * 0.3);
    p.y += cos(p.x - iTime * uSpeed * 0.4) + sin(p.y + iTime * uSpeed * 0.6);
    p.z += sin(iTime * uSpeed * 0.2) * 1.5;

    p.x *= uScaleX;
    p.y *= uScaleY;

    v = cos(p) - sin(p).yzx;

    vec3 shape = mix(max(v, v.yzx * 0.2), v, uRoundness);

    z += d = 1e-4 + 0.5 * length(shape);

    vec3 weights = abs(cos(p));
    weights /= dot(weights, vec3(1.0));

    vec3 customColor = uColor1 * weights.x + uColor2 * weights.y + uColor3 * weights.z;
    O.rgb += (customColor * uColorOffset) / d;
  }

  O /= O + 340.0;

  float luminance = dot(O.rgb, vec3(0.299, 0.587, 0.114));
  O.rgb = mix(vec3(luminance), O.rgb, 1.6);

  O.rgb += (random(I) - 0.5) / 128.0;
  O.a = 1.0;
}

void main() {
  mainImage(outColor, gl_FragCoord.xy);
}`

function compileShader(gl: WebGL2RenderingContext, type: number, source: string) {
  const shader = gl.createShader(type)
  if (!shader) return null
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    console.error(gl.getShaderInfoLog(shader))
    gl.deleteShader(shader)
    return null
  }
  return shader
}

export default function OrganicDarkBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const gl = canvas.getContext('webgl2', {
      alpha: true,
      antialias: true,
      depth: false,
      stencil: false,
      premultipliedAlpha: true,
    })
    if (!gl) return

    const vs = compileShader(gl, gl.VERTEX_SHADER, vertexShader)
    const fs = compileShader(gl, gl.FRAGMENT_SHADER, fragmentShader)
    if (!vs || !fs) return

    const program = gl.createProgram()
    if (!program) return
    gl.attachShader(program, vs)
    gl.attachShader(program, fs)
    gl.linkProgram(program)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      console.error(gl.getProgramInfoLog(program))
      return
    }

    const position = gl.getAttribLocation(program, 'a_position')
    const timeUniform = gl.getUniformLocation(program, 'iTime')
    const resolutionUniform = gl.getUniformLocation(program, 'iResolution')

    const buffer = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([
        -1, -1,
        1, -1,
        -1, 1,
        1, 1,
      ]),
      gl.STATIC_DRAW,
    )

    gl.useProgram(program)
    gl.enableVertexAttribArray(position)
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0)

    const resize = () => {
      const rect = canvas.getBoundingClientRect()
      const dpr = Math.min(window.devicePixelRatio || 1, 3)
      canvas.width = Math.max(1, Math.round(rect.width * dpr))
      canvas.height = Math.max(1, Math.round(rect.height * dpr))
      gl.viewport(0, 0, canvas.width, canvas.height)
    }

    resize()
    window.addEventListener('resize', resize)

    let raf = 0
    const start = performance.now()

    const render = (now: number) => {
      gl.useProgram(program)
      gl.uniform1f(timeUniform, (now - start) / 1000)
      gl.uniform2f(resolutionUniform, canvas.width, canvas.height)
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
      raf = requestAnimationFrame(render)
    }

    raf = requestAnimationFrame(render)

    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      if (buffer) gl.deleteBuffer(buffer)
      gl.deleteProgram(program)
      gl.deleteShader(vs)
      gl.deleteShader(fs)
    }
  }, [])

  return <canvas ref={canvasRef} className="organic-dark-bg" aria-hidden />
}
