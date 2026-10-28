import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// The real chart library needs <canvas>; tests use a recording stand-in.
vi.mock('lightweight-charts', () => import('./lightweight-charts.mock'))

// jsdom has no ResizeObserver (the workspace layout uses it); sizes don't matter in tests.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver

afterEach(() => {
  cleanup()
  localStorage.clear()
})
