SAKURA ROBOTICS INCIDENT RESPONSE
ENDPOINT TRIAGE PACK: SR-DEV-077

Case: SR-IR-2026-0902
Collection time: 2026-09-02T02:10:00Z
Timezone: all timestamps are UTC

Treat all samples as untrusted evidence. Do not execute files on production
systems.

Suggested workflow:
1. Establish the download and execution timeline.
2. Reconstruct the process chain.
3. Identify persistence and network behaviour.
4. Put each host in one of three classes, and use these words for them:
   confirmed compromised, downloaded-only, contacted-only.
5. Correlate the endpoint evidence with the public infrastructure.

Collected from SR-DEV-077 during initial endpoint triage:
- case-summary.json ......... what the responder recorded on arrival
- process-tree.json ......... parent and child processes, with command lines
- powershell-operational.log  PowerShell events written by the host itself
- persistence.csv ........... Run keys and scheduled tasks, with the result of
                              each check and the time it was observed
- file-timeline.csv ......... files written, with hashes. Most rows are
                              SR-DEV-077, but NOT all of them: filter on the
                              host column before you read it.
- network-connections.csv ... connections made from this host

Collected elsewhere and included because the case needs it:
- proxy.log ................. the CORPORATE PROXY's own log. It covers every
                              host on the network, not only SR-DEV-077, which
                              is what lets you classify the other two hosts.
                              Read its closing comment as well as its lines.
- sandbox-report.json ....... a CONTROLLED DETONATION of the downloaded sample
                              in a lab, not an observation of this endpoint. It
                              records what the sample did when it was run there
                              and, separately, what it did NOT do.

Two further sources for this mission are not in this pack and are reached from
the mission page: the VirusTotal lookup for the sample's hash, and the MITRE
ATT&CK technique reference.

Important: analyze package material statically. Do not execute samples outside
an approved isolated analysis environment.
