# Home network notes

## The router

The router is a Harbor AX3000 in the hall cupboard, next to the fibre box. Its settings page is at 192.168.77.1 and it is also managed from the Harbor app. The cupboard door has vents cut into it, because with the door shut and no vents it ran hot enough to restart itself in the summer.

The fibre line is from Northwind Fibre, 500 Mbps down and 100 up. Their status page is the first thing to check when everything is offline at once. The fibre box itself has no settings worth touching; its green light means the line is up.

It runs firmware 2.14.6. Automatic updates are on and happen at 04:00 on Sundays, which is why the network drops for two minutes then. If an update ever goes wrong, holding the reset button for ten seconds brings back the factory settings, and the settings saved to the app can then be restored in one step.

## Addresses

The router hands out addresses from 192.168.77.100 to 192.168.77.199. A few devices have fixed addresses outside that range so they never move:

- the printer in the study: 192.168.77.23
- the network storage box, called shelf: 192.168.77.40
- the mesh node in the garage: 192.168.77.3

## Wi-Fi

The main network is called Larkspur. Its 5 GHz band is pinned to channel 36 and the 2.4 GHz band to channel 6; the neighbours crowd channels 1 and 11, and letting the router pick moved us onto them every week.

Visitors use Larkspur-Guest. It runs on VLAN 30, so guests can reach the internet but none of our devices, and it switches itself off at midnight.

## The garage node

The garage has its own mesh node because the signal died at the back door. It is wired to the router with a flat cable under the hall carpet, so it does not share the airwaves with the devices it serves. Its light is blue when the link is good and amber when it has fallen back to wireless.

## Backups

The storage box backs up both laptops every night at 02:30 and keeps 14 daily copies and 8 weekly ones. Photos are also copied to an external drive on the first Sunday of every month; that drive lives in the desk drawer, not next to the box.

## When things break

If the printer vanishes from the laptops, it has usually lost its address: switch it off and on again, and it asks the router again. If the bedroom is slow in the evening, check that the 5 GHz band is still on channel 36. If nothing at all works, look at the Northwind Fibre status page before restarting anything.
