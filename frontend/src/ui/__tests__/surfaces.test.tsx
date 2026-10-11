// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { EmptyState, Notice, Skeleton, Working } from '../Feedback'
import { deltaDirection } from '../delta'
import { Metric } from '../Metric'
import { SlideValue } from '../SlideValue'
import { Surface } from '../Surface'

afterEach(cleanup)

describe('feedback', () => {
  it('announces a notice only when asked to', () => {
    const { rerender } = render(<Notice title="Saved" />)
    expect(screen.queryByRole('status')).toBeNull()
    rerender(<Notice title="Saved" live />)
    expect(screen.getByRole('status').textContent).toContain('Saved')
    rerender(<Notice tone="critical" title="Couldn't save" live />)
    expect(screen.getByRole('alert').textContent).toContain("Couldn't save")
  })

  it('says what is empty and offers the next step', () => {
    render(<EmptyState title="No voices yet" action={<button type="button">Add a voice</button>} />)
    expect(screen.getByText('No voices yet')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Add a voice' })).toBeTruthy()
  })

  it('names the work in progress to assistive technology', () => {
    render(<Working>Reading your documents</Working>)
    expect(screen.getByRole('status').textContent).toBe('Reading your documents')
  })

  it('keeps skeletons out of the accessibility tree', () => {
    const { container } = render(<Skeleton width={120} />)
    expect(container.firstElementChild?.getAttribute('aria-hidden')).toBe('true')
  })
})

describe('figures', () => {
  it('reads the direction of a change from its sign', () => {
    expect(deltaDirection('+12 ms')).toBe('up')
    expect(deltaDirection('−3%')).toBe('down')
    expect(deltaDirection('-3%')).toBe('down')
    expect(deltaDirection('0')).toBe('flat')
  })

  it('shows a metric exactly as given, with its unit and change', () => {
    render(<Metric label="First audio" value="412" unit="ms" delta="−38 ms" />)
    expect(screen.getByText('First audio')).toBeTruthy()
    expect(screen.getByText('412')).toBeTruthy()
    expect(screen.getByText('ms')).toBeTruthy()
    expect(screen.getByText('−38 ms').getAttribute('data-direction')).toBe('down')
  })

  it('replaces the figure when it changes rather than editing it in place', () => {
    const { rerender } = render(<SlideValue value="3" />)
    const before = screen.getByText('3')
    rerender(<SlideValue value="4" />)
    expect(screen.getByText('4')).not.toBe(before)
  })
})

describe('Surface', () => {
  it('renders as the element asked for', () => {
    render(
      <Surface as="section" aria-label="Voices">
        body
      </Surface>,
    )
    expect(screen.getByRole('region', { name: 'Voices' })).toBeTruthy()
  })

  it('writes the pointer position for the sheen on interactive surfaces', () => {
    render(
      <Surface material="interactive" data-testid="card">
        body
      </Surface>,
    )
    const card = screen.getByTestId('card')
    card.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 100 }) as DOMRect
    fireEvent.pointerMove(card, { clientX: 50, clientY: 25 })
    expect(card.style.getPropertyValue('--sheen-x')).toBe('25%')
    expect(card.style.getPropertyValue('--sheen-y')).toBe('25%')
  })
})
