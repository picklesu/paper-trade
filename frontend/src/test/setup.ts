import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// The real chart library needs <canvas>; tests use a recording stand-in.
vi.mock('lightweight-charts', () => import('./lightweight-charts.mock'))

afterEach(cleanup)
