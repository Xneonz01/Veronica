// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stubTopLayer } from '../../ui/__tests__/topLayer'
import { ProjectList } from '../ProjectList'
import { Recents } from '../Recents'
import { conversation, fakeWorkspace } from './fixtures'
import { Providers } from './Providers'

beforeEach(() => {
  stubTopLayer()
})
afterEach(cleanup)

const items = [conversation(1, 'Roof repairs', 3), conversation(2, 'Weekly plan'), conversation(3, 'Tax forms', 3)]

// The menu sits in a [popover], which jsdom always keeps at display: none, so it
// is found among hidden elements.
async function openMenu(title: string) {
  await userEvent.click(screen.getByRole('button', { name: `Options for “${title}”` }))
  return screen.getByRole('menu', { hidden: true })
}

function item(menu: HTMLElement, name: string) {
  return within(menu).getByRole('menuitem', { name, hidden: true })
}

describe('Recents', () => {
  it('lists conversations under the day they were last active, the open one marked', () => {
    render(
      <Providers workspace={fakeWorkspace({ conversations: items, openConversationId: 2 })}>
        <Recents />
      </Providers>,
    )
    expect(screen.getByRole('heading', { name: 'Today' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Weekly plan' }).getAttribute('aria-current')).toBe('true')
  })

  it("shows only a project's own conversations while it is chosen, with a way back", async () => {
    const workspace = fakeWorkspace({
      conversations: items,
      projects: [{ id: 3, name: 'House', conversation_count: 2 }],
      activeProjectId: 3,
    })
    render(
      <Providers workspace={workspace}>
        <Recents />
      </Providers>,
    )
    expect(screen.getByRole('heading', { name: 'House' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Weekly plan' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Show all conversations' }))
    expect(workspace.selectProject).toHaveBeenCalledWith(null)
  })

  it('renames in place: Enter keeps the new name, Escape the old one', async () => {
    const workspace = fakeWorkspace({ conversations: items })
    render(
      <Providers workspace={workspace}>
        <Recents />
      </Providers>,
    )
    await userEvent.click(item(await openMenu('Weekly plan'), 'Rename'))
    const field = screen.getByRole('textbox', { name: 'Conversation name' })
    await userEvent.clear(field)
    await userEvent.type(field, 'Plan for the week{Enter}')
    expect(workspace.renameConversation).toHaveBeenCalledWith(2, 'Plan for the week')

    await userEvent.click(item(await openMenu('Tax forms'), 'Rename'))
    await userEvent.type(screen.getByRole('textbox', { name: 'Conversation name' }), 'Nope{Escape}')
    expect(workspace.renameConversation).toHaveBeenCalledTimes(1)
  })

  it('keeps the old name for a blank one, and says so when a rename does not stick', async () => {
    const renameConversation = vi.fn(async () => false)
    const toast = vi.fn()
    render(
      <Providers workspace={fakeWorkspace({ conversations: items, renameConversation })} toast={toast}>
        <Recents />
      </Providers>,
    )
    await userEvent.click(item(await openMenu('Weekly plan'), 'Rename'))
    await userEvent.clear(screen.getByRole('textbox', { name: 'Conversation name' }))
    await userEvent.keyboard('   {Enter}')
    expect(renameConversation).not.toHaveBeenCalled()

    await userEvent.click(item(await openMenu('Weekly plan'), 'Rename'))
    await userEvent.type(screen.getByRole('textbox', { name: 'Conversation name' }), ' again{Enter}')
    expect(renameConversation).toHaveBeenCalledWith(2, 'Weekly plan again')
    await vi.waitFor(() => expect(toast).toHaveBeenCalledWith(expect.objectContaining({ tone: 'critical', title: "Couldn't rename it" })))
  })

  it('hands the keyboard back to the options button when a rename ends, without reopening it', async () => {
    render(
      <Providers workspace={fakeWorkspace({ conversations: items })}>
        <Recents />
      </Providers>,
    )
    const options = () => screen.getByRole('button', { name: 'Options for “Weekly plan”' })
    await userEvent.click(item(await openMenu('Weekly plan'), 'Rename'))
    await userEvent.keyboard('{Escape}')
    expect(document.activeElement).toBe(options())
    await userEvent.click(item(await openMenu('Weekly plan'), 'Rename'))
    await userEvent.keyboard('{Enter}')
    expect(document.activeElement).toBe(options())
    expect(screen.queryByRole('menu', { hidden: true })).toBeNull()
  })

  it('says why the list is missing instead of calling it empty, and tries again', async () => {
    const workspace = fakeWorkspace({ conversationsError: "Can't reach Veronica right now." })
    render(
      <Providers workspace={workspace}>
        <Recents />
        <ProjectList />
      </Providers>,
    )
    expect(screen.getByText("Can't reach Veronica right now.")).toBeTruthy()
    expect(screen.queryByText('Your conversations will be listed here.')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(workspace.reloadLists).toHaveBeenCalledOnce()
  })

  it('does the same for projects that could not be loaded', () => {
    render(
      <Providers workspace={fakeWorkspace({ projectsError: "Couldn't load your projects." })}>
        <ProjectList />
      </Providers>,
    )
    expect(screen.getByText("Couldn't load your projects.")).toBeTruthy()
    expect(screen.queryByText('Group conversations and the files she can cite from them.')).toBeNull()
  })

  it('deletes only after asking, and says so when it fails', async () => {
    const removeConversation = vi.fn(async () => false)
    render(
      <Providers workspace={fakeWorkspace({ conversations: items, removeConversation })}>
        <Recents />
      </Providers>,
    )
    await userEvent.click(item(await openMenu('Roof repairs'), 'Delete'))
    const dialog = screen.getByRole('dialog', { name: 'Delete this conversation?' })
    expect(dialog.textContent).toContain('“Roof repairs”')
    expect(removeConversation).not.toHaveBeenCalled()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))
    expect(removeConversation).toHaveBeenCalledWith(1)
    expect(within(dialog).getByRole('alert').textContent).toContain("Couldn't delete it")
  })
})
