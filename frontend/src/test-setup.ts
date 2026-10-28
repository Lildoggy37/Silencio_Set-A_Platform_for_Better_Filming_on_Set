import { cleanup, configure } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// Cold transformation of lazy-loaded UI modules is not a latency benchmark.
configure({ asyncUtilTimeout: 5000 })

// jsdom has no layout engine. These tests assert form/state behavior only;
// observing size is inert here and does not claim browser layout coverage.
Object.defineProperty(globalThis, 'ResizeObserver', { writable: true, configurable: true, value: class {
  observe() {}
  unobserve() {}
  disconnect() {}
} })

afterEach(() => { cleanup(); vi.unstubAllGlobals() })
Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation(query => ({
  matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
  addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
})) })
