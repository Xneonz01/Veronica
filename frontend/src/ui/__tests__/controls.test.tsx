// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Button, IconButton } from '../Button'
import { SearchField, SelectField, TextArea, TextField } from '../Field'
import { Segmented } from '../Segmented'
import { Switch } from '../Switch'

afterEach(cleanup)

describe('Button', () => {
  it('is a plain button unless told otherwise, so it never submits a form by accident', () => {
    render(<Button>Save</Button>)
    expect(screen.getByRole('button', { name: 'Save' })).toHaveProperty('type', 'button')
  })

  it('stops taking clicks while loading and says it is busy', async () => {
    const click = vi.fn()
    render(
      <Button loading onClick={click} trailingIcon={<span>after</span>}>
        Save
      </Button>,
    )
    const button = screen.getByRole('button', { name: 'Save' })
    expect(button).toHaveProperty('disabled', true)
    expect(button.getAttribute('aria-busy')).toBe('true')
    expect(screen.queryByText('after')).toBeNull()
    await userEvent.click(button)
    expect(click).not.toHaveBeenCalled()
  })

  it('names an icon button by its label and reports its pressed state', () => {
    render(<IconButton label="Mute" icon={<svg />} pressed />)
    const button = screen.getByRole('button', { name: 'Mute' })
    expect(button.getAttribute('aria-pressed')).toBe('true')
    expect(button.getAttribute('title')).toBe('Mute')
  })
})

describe('fields', () => {
  it('labels the control and links its hint and error', () => {
    render(<TextField label="Name" hint="As she'll say it" error="Give it a name." />)
    const input = screen.getByRole('textbox', { name: 'Name' })
    expect(input.getAttribute('aria-invalid')).toBe('true')
    const described = input.getAttribute('aria-describedby')?.split(' ') ?? []
    const texts = described.map((id) => document.getElementById(id)?.textContent)
    expect(texts).toEqual(["As she'll say it", 'Give it a name.'])
    expect(screen.getByRole('alert').textContent).toBe('Give it a name.')
  })

  it('is valid, and describes nothing, without a hint or an error', () => {
    render(<TextArea label="Notes" />)
    const area = screen.getByRole('textbox', { name: 'Notes' })
    expect(area.hasAttribute('aria-invalid')).toBe(false)
    expect(area.hasAttribute('aria-describedby')).toBe(false)
  })

  it('offers its options in a native select', async () => {
    const change = vi.fn()
    render(
      <SelectField
        label="Voice"
        options={[
          { value: 'rowan', label: 'Rowan' },
          { value: 'morgan', label: 'Morgan' },
        ]}
        onChange={(event) => change(event.target.value)}
      />,
    )
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Voice' }), 'morgan')
    expect(change).toHaveBeenCalledWith('morgan')
  })

  it('keeps a search box named for screen readers with the label off screen', () => {
    render(<SearchField label="Search conversations" />)
    expect(screen.getByRole('searchbox', { name: 'Search conversations' })).toBeTruthy()
  })
})

describe('Switch', () => {
  it('is a named, described switch that flips', async () => {
    const change = vi.fn()
    render(<Switch label="Wake word" description="Listen for her name" checked={false} onChange={change} />)
    const toggle = screen.getByRole('switch', { name: 'Wake word' })
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    expect(document.getElementById(toggle.getAttribute('aria-describedby') ?? '')?.textContent).toBe(
      'Listen for her name',
    )
    await userEvent.click(toggle)
    expect(change).toHaveBeenCalledWith(true)
  })

  it("doesn't flip while disabled", async () => {
    const change = vi.fn()
    render(<Switch label="Wake word" checked onChange={change} disabled />)
    await userEvent.click(screen.getByRole('switch'))
    expect(change).not.toHaveBeenCalled()
  })
})

describe('Segmented', () => {
  function Theme() {
    const [value, setValue] = useState<'system' | 'light' | 'dark'>('system')
    return (
      <Segmented
        label="Theme"
        value={value}
        onChange={setValue}
        options={[
          { value: 'system', label: 'System' },
          { value: 'light', label: 'Light' },
          { value: 'dark', label: 'Dark' },
        ]}
      />
    )
  }

  it('is one tab stop: only the chosen option is reachable with Tab', () => {
    render(<Theme />)
    const tabbable = screen.getAllByRole('radio').filter((radio) => radio.tabIndex === 0)
    expect(tabbable.map((radio) => radio.textContent)).toEqual(['System'])
  })

  it('moves the choice and the focus with the arrow keys, wrapping, and jumps with Home and End', async () => {
    render(<Theme />)
    await userEvent.tab()
    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('radio', { checked: true }).textContent).toBe('Light')
    expect(document.activeElement?.textContent).toBe('Light')
    await userEvent.keyboard('{ArrowRight}{ArrowRight}')
    expect(screen.getByRole('radio', { checked: true }).textContent).toBe('System')
    await userEvent.keyboard('{End}')
    expect(screen.getByRole('radio', { checked: true }).textContent).toBe('Dark')
    await userEvent.keyboard('{Home}')
    expect(document.activeElement?.textContent).toBe('System')
  })
})
