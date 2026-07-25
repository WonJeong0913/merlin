# Deterministic selection run — queries as sent

Every query below is the exact string handed to `GovernedProvisioner`.
Read this to check the task was conveyed faithfully; the oracle column
is upstream ground truth, not ours.

- library: 209 skills
- oracle: `experiments/skillsbench/readiness-87.json:curated_skill_variants`
- routing mode: `deterministic` (lexical only)

## mechanical, k=1 — query rule: task_id with hyphens replaced by spaces

| task_id | query as sent | oracle | provisioned | hit |
|---|---|---|---|---|
| `3d-scan-calc` | `3d scan calc` | mesh-analysis | mesh-analysis | yes |
| `ada-bathroom-plan-repair` | `ada bathroom plan repair` | ada-plan-view-accessibility, architectural-dxf-extraction, geometric-layout-repair | ada-plan-view-accessibility | yes |
| `adaptive-cruise-control` | `adaptive cruise control` | csv-processing, pid-controller, simulation-metrics, vehicle-dynamics, yaml-config | pid-controller | yes |
| `azure-bgp-oscillation-route-leak` | `azure bgp oscillation route leak` | azure-bgp | azure-bgp | yes |
| `bike-rebalance` | `bike rebalance` | geospatial-routing-data, logistics-rules-to-optimization, routing-subtour-elimination, scip-opt | (none) | no |
| `citation-check` | `citation check` | citation-management | browser-testing | no |
| `civ6-adjacency-optimizer` | `civ6 adjacency optimizer` | civ6lib, hex-grid-spatial, map-optimization-strategy, sqlite-map-parser | civ6lib | yes |
| `court-form-filling` | `court form filling` | pdf | pdf | yes |
| `crystallographic-wyckoff-position-analysis` | `crystallographic wyckoff position analysis` | pymatgen, sympy | 13f-analyzer | no |
| `dapt-intrusion-detection` | `dapt intrusion detection` | pcap-analysis, threat-detection | energy-calculator | no |
| `data-to-d3` | `data to d3` | d3-visualization | d3-visualization | yes |
| `debug-trl-grpo` | `debug trl grpo` | grpo, rl-post-training, trl | trl | yes |
| `dialogue-parser` | `dialogue parser` | dialogue-graph | dialogue-graph | yes |
| `drone-planning-control` | `drone planning control` | attitude-controller-planner, flight-plan-parser, motor-model-dynamics, plot-quadrotor, position-controller-trajectory-planner, stepinfo-3d | attitude-controller-planner | yes |
| `dynamic-object-aware-egomotion` | `dynamic object aware egomotion` | dyn-object-masks, egomotion-estimation, output-validation, sampling-and-indexing | dyn-object-masks | yes |
| `earthquake-phase-association` | `earthquake phase association` | gamma-phase-associator, obspy-data-api, seisbench-model-api, seismic-picker-selection | gamma-phase-associator | yes |
| `earthquake-plate-calculation` | `earthquake plate calculation` | geospatial-analysis | geospatial-analysis | yes |
| `econ-detrending-correlation` | `econ detrending correlation` | timeseries-detrending | timeseries-detrending | yes |
| `edit-pdf` | `edit pdf` | pdf-editing, text-parser | academic-pdf-redaction | no |
| `energy-ac-optimal-power-flow` | `energy ac optimal power flow` | ac-branch-pi-model, casadi-ipopt-nlp, power-flow-data | ac-branch-pi-model | yes |
| `energy-market-pricing` | `energy market pricing` | dc-power-flow, economic-dispatch, locational-marginal-prices, power-flow-data | audio-extractor | no |
| `energy-unit-commitment` | `energy unit commitment` | milp-solver-workflow, unit-commitment-data-modeling, unit-commitment-operating-rules | unit-commitment-data-modeling | yes |
| `enterprise-information-search` | `enterprise information search` | enterprise-artifact-search | citation-management | no |
| `exam-block-sequencing` | `exam block sequencing` | mip-solver-and-solution-audit, ordered-window-sequencing-mip | ordered-window-sequencing-mip | yes |
| `exceltable-in-ppt` | `exceltable in ppt` | pptx, xlsx@1a801fe1bd5f | (none) | no |
| `exoplanet-detection-period` | `exoplanet detection period` | box-least-squares, exoplanet-workflows, light-curve-preprocessing, lomb-scargle-periodogram, transit-least-squares | exoplanet-workflows | yes |
| `financial-modeling-qa` | `financial modeling qa` | pdf@0d4af66f868d, xlsx@b1a6bde518fc | obspy-data-api | no |
| `fix-build-agentops` | `fix build agentops` | analyze-ci, temporal-python-testing, testing-python, uv-package-manager | d3-visualization | no |
| `fix-build-google-auto` | `fix build google auto` | maven-build-lifecycle, maven-dependency-management, maven-plugin-configuration | citation-management | no |
| `fix-druid-loophole-cve` | `fix druid loophole cve` | jackson-security, senior-java | lab-unit-harmonization | no |
| `fix-erlang-ssh-cve` | `fix erlang ssh cve` | erlang-concurrency, erlang-distribution, erlang-otp-behaviors, find-bugs, senior-security, ssh-penetration-testing | erlang-concurrency | yes |
| `fix-visual-stability` | `fix visual stability` | browser-testing, react-best-practices, web-interface-guidelines | browser-testing | yes |
| `flink-query` | `flink query` | pdf, senior-data-engineer | hibernate-upgrade | no |
| `flood-risk-analysis` | `flood risk analysis` | flood-detection, nws-flood-thresholds, usgs-data-download | 13f-analyzer | no |
| `glm-lake-mendota` | `glm lake mendota` | glm-basics, glm-calibration, glm-output | glm-basics | yes |
| `gravitational-wave-detection` | `gravitational wave detection` | conditioning, matched-filtering | matched-filtering | yes |
| `grid-dispatch-operator` | `grid dispatch operator` | dc-power-flow, economic-dispatch, power-flow-data | economic-dispatch | yes |
| `hvac-control` | `hvac control` | excitation-signal-design, first-order-model-fitting, imc-tuning-rules, safety-interlocks, scipy-curve-fit | attitude-controller-planner | no |
| `invoice-fraud-detection` | `invoice fraud detection` | fuzzy-match, pdf, xlsx | energy-calculator | no |
| `jax-computing-basics` | `jax computing basics` | jax-skills | jax-skills | yes |
| `jpg-ocr-stat` | `jpg ocr stat` | image-ocr, openai-vision, pdf, video-frame-extraction, xlsx | image-ocr | yes |
| `lab-unit-harmonization` | `lab unit harmonization` | lab-unit-harmonization | lab-unit-harmonization | yes |
| `lake-warming-attribution` | `lake warming attribution` | contribution-analysis, meteorology-driver-classification, pca-decomposition, trend-analysis | glm-basics | no |
| `latex-formula-extraction` | `latex formula extraction` | marker, pdf | enterprise-artifact-search | no |
| `lean4-proof` | `lean4 proof` | lean4-memories, lean4-theorem-proving | lean4-memories | yes |
| `llm-prefix-cache-replay` | `llm prefix cache replay` | cache-policy-comparison, prefix-cache-replay | prefix-cache-replay | yes |
| `manufacturing-codebook-normalization` | `manufacturing codebook normalization` | manufacturing-failure-reason-codebook-normalization | text-to-speech | no |
| `manufacturing-equipment-maintenance` | `manufacturing equipment maintenance` | reflow-machine-maintenance-guidance, reflow-profile-compliance-toolkit | reflow-machine-maintenance-guidance | yes |
| `manufacturing-fjsp-optimization` | `manufacturing fjsp optimization` | fjsp-baseline-repair-with-downtime-and-policy | casadi-ipopt-nlp | no |
| `mario-coin-counting` | `mario coin counting` | ffmpeg-keyframe-extraction, image-editing, object-counter | flood-detection | no |
| `mars-clouds-clustering` | `mars clouds clustering` | custom-distance-metrics, parallel-processing, pareto-optimization | custom-distance-metrics | yes |
| `multilingual-video-dubbing` | `multilingual video dubbing` | ffmpeg-audio-processing, ffmpeg-format-conversion, ffmpeg-media-info, ffmpeg-video-editing, ffmpeg-video-filters, text-to-speech | audio-extractor | no |
| `offer-letter-generator` | `offer letter generator` | docx | economic-dispatch | no |
| `organize-messy-files` | `organize messy files` | docx@d3cfe519dca2, file-organizer, pdf, planning-with-files, pptx@3ad72806f38c | architectural-dxf-extraction | no |
| `paper-anonymizer` | `paper anonymizer` | academic-pdf-redaction, pdf@31b77156e562 | transaction-protocol-reasoning | no |
| `parallel-tfidf-search` | `parallel tfidf search` | memory-optimization, python-parallelization, workload-balancing | parallel-processing | no |
| `paratransit-routing` | `paratransit routing` | ortools-pickup-delivery-routing, ortools-routing-modeling | ortools-pickup-delivery-routing | yes |
| `pddl-airport-planning` | `pddl airport planning` | pddl-skills | pddl-skills | yes |
| `pddl-tpp-planning` | `pddl tpp planning` | pddl-skills@3a838435e9e2 | pddl-skills | no |
| `pdf-excel-diff` | `pdf excel diff` | pdf, xlsx | academic-pdf-redaction | no |
| `powerlifting-coef-calc` | `powerlifting coef calc` | powerlifting, senior-data-scientist, xlsx | powerlifting | yes |
| `pptx-reference-formatting` | `pptx reference formatting` | pptx@5f3f8d7402fa | python-scala-syntax-mapping | no |
| `protein-expression-analysis` | `protein expression analysis` | xlsx | 13f-analyzer | no |
| `python-scala-translation` | `python scala translation` | python-scala-collections, python-scala-functional, python-scala-idioms, python-scala-libraries, python-scala-oop, python-scala-syntax-mapping | python-scala-idioms | yes |
| `quantum-numerical-simulation` | `quantum numerical simulation` | qutip | csv-processing | no |
| `r2r-mpc-control` | `r2r mpc control` | finite-horizon-lqr, integral-action-design, mpc-horizon-tuning, state-space-linearization | attitude-controller-planner | no |
| `radar-vital-signs` | `radar vital signs` | radar-signal-processing, radar-vital-signs, vital-sign-extraction | radar-signal-processing | yes |
| `react-performance-debugging` | `react performance debugging` | browser-testing@ebcae60ef709, react-best-practices@175ee538059c | ac-branch-pi-model | no |
| `reserves-at-risk-calc` | `reserves at risk calc` | xlsx | (none) | no |
| `sales-pivot-analysis` | `sales pivot analysis` | pdf@a963d991212a, xlsx@8e5fe91c81dc | 13f-analyzer | no |
| `sec-financial-report` | `sec financial report` | 13f-analyzer, fuzzy-name-search | 13f-analyzer | yes |
| `seismic-phase-picking` | `seismic phase picking` | obspy-data-api, obspy-datacenter-client, seisbench-model-api, seismic-picker-selection | seisbench-model-api | yes |
| `setup-fuzzing-py` | `setup fuzzing py` | discover-important-function, fuzzing-python, setup-env | silence-detector | no |
| `shock-analysis-demand` | `shock analysis demand` | xlsx | 13f-analyzer | no |
| `shock-analysis-supply` | `shock analysis supply` | xlsx | 13f-analyzer | no |
| `simpo-code-reproduction` | `simpo code reproduction` | nlp-research-repo-package-installment, pdf@ba530d0bb107 | nlp-research-repo-package-installment | yes |
| `software-dependency-audit` | `software dependency audit` | cvss-score-extraction, trivy-offline-vulnerability-scanning, vulnerability-csv-reporting | find-bugs | no |
| `spring-boot-jakarta-migration` | `spring boot jakarta migration` | hibernate-upgrade, jakarta-namespace, restclient-migration, spring-boot-migration, spring-security-6 | jakarta-namespace | yes |
| `suricata-custom-exfil` | `suricata custom exfil` | pcap-triage-tshark, suricata-offline-evejson, suricata-rules-basics | custom-distance-metrics | no |
| `syzkaller-ppdev-syzlang` | `syzkaller ppdev syzlang` | syz-extract-constants, syzkaller-build-loop, syzlang-ioctl-basics | syz-extract-constants | yes |
| `threejs-structure-parser` | `threejs structure parser` | obj-exporter, threejs | marker | no |
| `threejs-to-obj` | `threejs to obj` | obj-exporter@b6d1bcf98031, threejs | obj-exporter | no |
| `tictoc-unnecessary-abort-detection` | `tictoc unnecessary abort detection` | transaction-concurrency-control-foundations, transaction-protocol-reasoning, transaction-trace-analysis | energy-calculator | no |
| `travel-planning` | `travel planning` | search-accommodations, search-attractions, search-cities, search-driving-distance, search-flights, search-restaurants | attitude-controller-planner | no |
| `video-silence-remover` | `video silence remover` | audio-extractor, energy-calculator, pause-detector, report-generator, segment-combiner, silence-detector, video-processor | silence-detector | yes |
| `weighted-gdp-calc` | `weighted gdp calc` | xlsx | (none) | no |
| `xlsx-recover-data` | `xlsx recover data` | data-reconciliation, xlsx | xlsx | yes |

## mechanical, k=3 — query rule: task_id with hyphens replaced by spaces

| task_id | query as sent | oracle | provisioned | hit |
|---|---|---|---|---|
| `3d-scan-calc` | `3d scan calc` | mesh-analysis | mesh-analysis, obj-exporter, stepinfo-3d | yes |
| `ada-bathroom-plan-repair` | `ada bathroom plan repair` | ada-plan-view-accessibility, architectural-dxf-extraction, geometric-layout-repair | ada-plan-view-accessibility, architectural-dxf-extraction, fjsp-baseline-repair-with-downtime-and-policy | yes |
| `adaptive-cruise-control` | `adaptive cruise control` | csv-processing, pid-controller, simulation-metrics, vehicle-dynamics, yaml-config | attitude-controller-planner, pid-controller, vehicle-dynamics | yes |
| `azure-bgp-oscillation-route-leak` | `azure bgp oscillation route leak` | azure-bgp | azure-bgp, geospatial-routing-data, ortools-routing-modeling | yes |
| `bike-rebalance` | `bike rebalance` | geospatial-routing-data, logistics-rules-to-optimization, routing-subtour-elimination, scip-opt | (none) | no |
| `citation-check` | `citation check` | citation-management | browser-testing, citation-management, output-validation | yes |
| `civ6-adjacency-optimizer` | `civ6 adjacency optimizer` | civ6lib, hex-grid-spatial, map-optimization-strategy, sqlite-map-parser | civ6lib | yes |
| `court-form-filling` | `court form filling` | pdf | pdf | yes |
| `crystallographic-wyckoff-position-analysis` | `crystallographic wyckoff position analysis` | pymatgen, sympy | 13f-analyzer, dc-power-flow, energy-calculator | no |
| `dapt-intrusion-detection` | `dapt intrusion detection` | pcap-analysis, threat-detection | energy-calculator, exoplanet-workflows, matched-filtering | no |
| `data-to-d3` | `data to d3` | d3-visualization | 13f-analyzer, conditioning, d3-visualization | yes |
| `debug-trl-grpo` | `debug trl grpo` | grpo, rl-post-training, trl | grpo, radar-signal-processing, trl | yes |
| `dialogue-parser` | `dialogue parser` | dialogue-graph | dialogue-graph | yes |
| `drone-planning-control` | `drone planning control` | attitude-controller-planner, flight-plan-parser, motor-model-dynamics, plot-quadrotor, position-controller-trajectory-planner, stepinfo-3d | attitude-controller-planner, excitation-signal-design, position-controller-trajectory-planner | yes |
| `dynamic-object-aware-egomotion` | `dynamic object aware egomotion` | dyn-object-masks, egomotion-estimation, output-validation, sampling-and-indexing | dyn-object-masks, finite-horizon-lqr, first-order-model-fitting | yes |
| `earthquake-phase-association` | `earthquake phase association` | gamma-phase-associator, obspy-data-api, seisbench-model-api, seismic-picker-selection | gamma-phase-associator, seisbench-model-api, seismic-picker-selection | yes |
| `earthquake-plate-calculation` | `earthquake plate calculation` | geospatial-analysis | audio-extractor, gamma-phase-associator, geospatial-analysis | yes |
| `econ-detrending-correlation` | `econ detrending correlation` | timeseries-detrending | qutip, timeseries-detrending | yes |
| `edit-pdf` | `edit pdf` | pdf-editing, text-parser | academic-pdf-redaction, marker, pdf | no |
| `energy-ac-optimal-power-flow` | `energy ac optimal power flow` | ac-branch-pi-model, casadi-ipopt-nlp, power-flow-data | ac-branch-pi-model, dc-power-flow, economic-dispatch | yes |
| `energy-market-pricing` | `energy market pricing` | dc-power-flow, economic-dispatch, locational-marginal-prices, power-flow-data | audio-extractor, energy-calculator, silence-detector | no |
| `energy-unit-commitment` | `energy unit commitment` | milp-solver-workflow, unit-commitment-data-modeling, unit-commitment-operating-rules | audio-extractor, unit-commitment-data-modeling, unit-commitment-operating-rules | yes |
| `enterprise-information-search` | `enterprise information search` | enterprise-artifact-search | citation-management, enterprise-artifact-search, fuzzy-name-search | yes |
| `exam-block-sequencing` | `exam block sequencing` | mip-solver-and-solution-audit, ordered-window-sequencing-mip | ordered-window-sequencing-mip, prefix-cache-replay | yes |
| `exceltable-in-ppt` | `exceltable in ppt` | pptx, xlsx@1a801fe1bd5f | (none) | no |
| `exoplanet-detection-period` | `exoplanet detection period` | box-least-squares, exoplanet-workflows, light-curve-preprocessing, lomb-scargle-periodogram, transit-least-squares | energy-calculator, exoplanet-workflows, light-curve-preprocessing | yes |
| `financial-modeling-qa` | `financial modeling qa` | pdf@0d4af66f868d, xlsx@b1a6bde518fc | obspy-data-api, scip-opt, senior-data-engineer | no |
| `fix-build-agentops` | `fix build agentops` | analyze-ci, temporal-python-testing, testing-python, uv-package-manager | d3-visualization, lab-unit-harmonization, lean4-theorem-proving | no |
| `fix-build-google-auto` | `fix build google auto` | maven-build-lifecycle, maven-dependency-management, maven-plugin-configuration | citation-management, d3-visualization, lab-unit-harmonization | no |
| `fix-druid-loophole-cve` | `fix druid loophole cve` | jackson-security, senior-java | lab-unit-harmonization, memory-optimization | no |
| `fix-erlang-ssh-cve` | `fix erlang ssh cve` | erlang-concurrency, erlang-distribution, erlang-otp-behaviors, find-bugs, senior-security, ssh-penetration-testing | erlang-concurrency, erlang-distribution, erlang-otp-behaviors | yes |
| `fix-visual-stability` | `fix visual stability` | browser-testing, react-best-practices, web-interface-guidelines | browser-testing, ffmpeg-video-filters, web-interface-guidelines | yes |
| `flink-query` | `flink query` | pdf, senior-data-engineer | hibernate-upgrade, senior-data-engineer | yes |
| `flood-risk-analysis` | `flood risk analysis` | flood-detection, nws-flood-thresholds, usgs-data-download | 13f-analyzer, dc-power-flow, energy-calculator | no |
| `glm-lake-mendota` | `glm lake mendota` | glm-basics, glm-calibration, glm-output | glm-basics, glm-calibration, glm-output | yes |
| `gravitational-wave-detection` | `gravitational wave detection` | conditioning, matched-filtering | conditioning, matched-filtering, radar-vital-signs | yes |
| `grid-dispatch-operator` | `grid dispatch operator` | dc-power-flow, economic-dispatch, power-flow-data | economic-dispatch, hex-grid-spatial, map-optimization-strategy | yes |
| `hvac-control` | `hvac control` | excitation-signal-design, first-order-model-fitting, imc-tuning-rules, safety-interlocks, scipy-curve-fit | attitude-controller-planner, excitation-signal-design, imc-tuning-rules | yes |
| `invoice-fraud-detection` | `invoice fraud detection` | fuzzy-match, pdf, xlsx | energy-calculator, exoplanet-workflows, matched-filtering | no |
| `jax-computing-basics` | `jax computing basics` | jax-skills | ac-branch-pi-model, dc-power-flow, jax-skills | yes |
| `jpg-ocr-stat` | `jpg ocr stat` | image-ocr, openai-vision, pdf, video-frame-extraction, xlsx | image-ocr | yes |
| `lab-unit-harmonization` | `lab unit harmonization` | lab-unit-harmonization | casadi-ipopt-nlp, lab-unit-harmonization, temporal-python-testing | yes |
| `lake-warming-attribution` | `lake warming attribution` | contribution-analysis, meteorology-driver-classification, pca-decomposition, trend-analysis | glm-basics, meteorology-driver-classification | yes |
| `latex-formula-extraction` | `latex formula extraction` | marker, pdf | enterprise-artifact-search, marker, milp-solver-workflow | yes |
| `lean4-proof` | `lean4 proof` | lean4-memories, lean4-theorem-proving | lean4-memories | yes |
| `llm-prefix-cache-replay` | `llm prefix cache replay` | cache-policy-comparison, prefix-cache-replay | cache-policy-comparison, ordered-window-sequencing-mip, prefix-cache-replay | yes |
| `manufacturing-codebook-normalization` | `manufacturing codebook normalization` | manufacturing-failure-reason-codebook-normalization | text-to-speech | no |
| `manufacturing-equipment-maintenance` | `manufacturing equipment maintenance` | reflow-machine-maintenance-guidance, reflow-profile-compliance-toolkit | reflow-machine-maintenance-guidance, safety-interlocks | yes |
| `manufacturing-fjsp-optimization` | `manufacturing fjsp optimization` | fjsp-baseline-repair-with-downtime-and-policy | casadi-ipopt-nlp, economic-dispatch, geospatial-routing-data | no |
| `mario-coin-counting` | `mario coin counting` | ffmpeg-keyframe-extraction, image-editing, object-counter | flood-detection | no |
| `mars-clouds-clustering` | `mars clouds clustering` | custom-distance-metrics, parallel-processing, pareto-optimization | custom-distance-metrics, gamma-phase-associator | yes |
| `multilingual-video-dubbing` | `multilingual video dubbing` | ffmpeg-audio-processing, ffmpeg-format-conversion, ffmpeg-media-info, ffmpeg-video-editing, ffmpeg-video-filters, text-to-speech | audio-extractor, ffmpeg-format-conversion, ffmpeg-keyframe-extraction | yes |
| `offer-letter-generator` | `offer letter generator` | docx | economic-dispatch, memory-optimization, power-flow-data | no |
| `organize-messy-files` | `organize messy files` | docx@d3cfe519dca2, file-organizer, pdf, planning-with-files, pptx@3ad72806f38c | architectural-dxf-extraction, audio-extractor, csv-processing | no |
| `paper-anonymizer` | `paper anonymizer` | academic-pdf-redaction, pdf@31b77156e562 | transaction-protocol-reasoning | no |
| `parallel-tfidf-search` | `parallel tfidf search` | memory-optimization, python-parallelization, workload-balancing | citation-management, enterprise-artifact-search, parallel-processing | no |
| `paratransit-routing` | `paratransit routing` | ortools-pickup-delivery-routing, ortools-routing-modeling | geospatial-routing-data, ortools-pickup-delivery-routing, ortools-routing-modeling | yes |
| `pddl-airport-planning` | `pddl airport planning` | pddl-skills | attitude-controller-planner, exoplanet-workflows, pddl-skills | yes |
| `pddl-tpp-planning` | `pddl tpp planning` | pddl-skills@3a838435e9e2 | attitude-controller-planner, exoplanet-workflows, pddl-skills | no |
| `pdf-excel-diff` | `pdf excel diff` | pdf, xlsx | academic-pdf-redaction, marker, pdf | yes |
| `powerlifting-coef-calc` | `powerlifting coef calc` | powerlifting, senior-data-scientist, xlsx | powerlifting | yes |
| `pptx-reference-formatting` | `pptx reference formatting` | pptx@5f3f8d7402fa | citation-management, fuzzy-match, python-scala-syntax-mapping | no |
| `protein-expression-analysis` | `protein expression analysis` | xlsx | 13f-analyzer, dc-power-flow, energy-calculator | no |
| `python-scala-translation` | `python scala translation` | python-scala-collections, python-scala-functional, python-scala-idioms, python-scala-libraries, python-scala-oop, python-scala-syntax-mapping | python-scala-collections, python-scala-functional, python-scala-idioms | yes |
| `quantum-numerical-simulation` | `quantum numerical simulation` | qutip | csv-processing, glm-basics, glm-calibration | no |
| `r2r-mpc-control` | `r2r mpc control` | finite-horizon-lqr, integral-action-design, mpc-horizon-tuning, state-space-linearization | attitude-controller-planner, excitation-signal-design, finite-horizon-lqr | yes |
| `radar-vital-signs` | `radar vital signs` | radar-signal-processing, radar-vital-signs, vital-sign-extraction | radar-signal-processing, radar-vital-signs, vital-sign-extraction | yes |
| `react-performance-debugging` | `react performance debugging` | browser-testing@ebcae60ef709, react-best-practices@175ee538059c | ac-branch-pi-model, browser-testing, grpo | no |
| `reserves-at-risk-calc` | `reserves at risk calc` | xlsx | (none) | no |
| `sales-pivot-analysis` | `sales pivot analysis` | pdf@a963d991212a, xlsx@8e5fe91c81dc | 13f-analyzer, dc-power-flow, energy-calculator | no |
| `sec-financial-report` | `sec financial report` | 13f-analyzer, fuzzy-name-search | 13f-analyzer | yes |
| `seismic-phase-picking` | `seismic phase picking` | obspy-data-api, obspy-datacenter-client, seisbench-model-api, seismic-picker-selection | ac-branch-pi-model, seisbench-model-api, seismic-picker-selection | yes |
| `setup-fuzzing-py` | `setup fuzzing py` | discover-important-function, fuzzing-python, setup-env | silence-detector, temporal-python-testing | no |
| `shock-analysis-demand` | `shock analysis demand` | xlsx | 13f-analyzer, dc-power-flow, energy-calculator | no |
| `shock-analysis-supply` | `shock analysis supply` | xlsx | 13f-analyzer, dc-power-flow, energy-calculator | no |
| `simpo-code-reproduction` | `simpo code reproduction` | nlp-research-repo-package-installment, pdf@ba530d0bb107 | find-bugs, memory-optimization, nlp-research-repo-package-installment | yes |
| `software-dependency-audit` | `software dependency audit` | cvss-score-extraction, trivy-offline-vulnerability-scanning, vulnerability-csv-reporting | find-bugs, maven-dependency-management, obj-exporter | no |
| `spring-boot-jakarta-migration` | `spring boot jakarta migration` | hibernate-upgrade, jakarta-namespace, restclient-migration, spring-boot-migration, spring-security-6 | hibernate-upgrade, jakarta-namespace, spring-boot-migration | yes |
| `suricata-custom-exfil` | `suricata custom exfil` | pcap-triage-tshark, suricata-offline-evejson, suricata-rules-basics | custom-distance-metrics, maven-plugin-configuration, obspy-data-api | no |
| `syzkaller-ppdev-syzlang` | `syzkaller ppdev syzlang` | syz-extract-constants, syzkaller-build-loop, syzlang-ioctl-basics | syz-extract-constants, syzkaller-build-loop, syzlang-ioctl-basics | yes |
| `threejs-structure-parser` | `threejs structure parser` | obj-exporter, threejs | marker, pymatgen | no |
| `threejs-to-obj` | `threejs to obj` | obj-exporter@b6d1bcf98031, threejs | obj-exporter, threejs | yes |
| `tictoc-unnecessary-abort-detection` | `tictoc unnecessary abort detection` | transaction-concurrency-control-foundations, transaction-protocol-reasoning, transaction-trace-analysis | energy-calculator, exoplanet-workflows, matched-filtering | no |
| `travel-planning` | `travel planning` | search-accommodations, search-attractions, search-cities, search-driving-distance, search-flights, search-restaurants | attitude-controller-planner, exoplanet-workflows, fjsp-baseline-repair-with-downtime-and-policy | no |
| `video-silence-remover` | `video silence remover` | audio-extractor, energy-calculator, pause-detector, report-generator, segment-combiner, silence-detector, video-processor | audio-extractor, energy-calculator, silence-detector | yes |
| `weighted-gdp-calc` | `weighted gdp calc` | xlsx | (none) | no |
| `xlsx-recover-data` | `xlsx recover data` | data-reconciliation, xlsx | 13f-analyzer, conditioning, xlsx | yes |

## mechanical, k=5 — query rule: task_id with hyphens replaced by spaces

| task_id | query as sent | oracle | provisioned | hit |
|---|---|---|---|---|
| `3d-scan-calc` | `3d scan calc` | mesh-analysis | mesh-analysis, obj-exporter, stepinfo-3d | yes |
| `ada-bathroom-plan-repair` | `ada bathroom plan repair` | ada-plan-view-accessibility, architectural-dxf-extraction, geometric-layout-repair | ada-plan-view-accessibility, architectural-dxf-extraction, fjsp-baseline-repair-with-downtime-and-policy, lean4-theorem-proving, pddl-skills | yes |
| `adaptive-cruise-control` | `adaptive cruise control` | csv-processing, pid-controller, simulation-metrics, vehicle-dynamics, yaml-config | attitude-controller-planner, excitation-signal-design, imc-tuning-rules, pid-controller, vehicle-dynamics | yes |
| `azure-bgp-oscillation-route-leak` | `azure bgp oscillation route leak` | azure-bgp | azure-bgp, geospatial-routing-data, ortools-routing-modeling, routing-subtour-elimination, search-flights | yes |
| `bike-rebalance` | `bike rebalance` | geospatial-routing-data, logistics-rules-to-optimization, routing-subtour-elimination, scip-opt | (none) | no |
| `citation-check` | `citation check` | citation-management | browser-testing, citation-management, output-validation | yes |
| `civ6-adjacency-optimizer` | `civ6 adjacency optimizer` | civ6lib, hex-grid-spatial, map-optimization-strategy, sqlite-map-parser | civ6lib | yes |
| `court-form-filling` | `court form filling` | pdf | pdf | yes |
| `crystallographic-wyckoff-position-analysis` | `crystallographic wyckoff position analysis` | pymatgen, sympy | 13f-analyzer, dc-power-flow, energy-calculator, exoplanet-workflows, ffmpeg-audio-processing | no |
| `dapt-intrusion-detection` | `dapt intrusion detection` | pcap-analysis, threat-detection | energy-calculator, exoplanet-workflows, matched-filtering, radar-vital-signs, segment-combiner | no |
| `data-to-d3` | `data to d3` | d3-visualization | 13f-analyzer, conditioning, csv-processing, cvss-score-extraction, d3-visualization | yes |
| `debug-trl-grpo` | `debug trl grpo` | grpo, rl-post-training, trl | grpo, radar-signal-processing, rl-post-training, trl | yes |
| `dialogue-parser` | `dialogue parser` | dialogue-graph | dialogue-graph | yes |
| `drone-planning-control` | `drone planning control` | attitude-controller-planner, flight-plan-parser, motor-model-dynamics, plot-quadrotor, position-controller-trajectory-planner, stepinfo-3d | attitude-controller-planner, excitation-signal-design, exoplanet-workflows, fjsp-baseline-repair-with-downtime-and-policy, position-controller-trajectory-planner | yes |
| `dynamic-object-aware-egomotion` | `dynamic object aware egomotion` | dyn-object-masks, egomotion-estimation, output-validation, sampling-and-indexing | dyn-object-masks, finite-horizon-lqr, first-order-model-fitting, jax-skills, memory-optimization | yes |
| `earthquake-phase-association` | `earthquake phase association` | gamma-phase-associator, obspy-data-api, seisbench-model-api, seismic-picker-selection | ac-branch-pi-model, gamma-phase-associator, geospatial-analysis, seisbench-model-api, seismic-picker-selection | yes |
| `earthquake-plate-calculation` | `earthquake plate calculation` | geospatial-analysis | audio-extractor, gamma-phase-associator, geospatial-analysis, obspy-datacenter-client, seisbench-model-api | yes |
| `econ-detrending-correlation` | `econ detrending correlation` | timeseries-detrending | qutip, timeseries-detrending | yes |
| `edit-pdf` | `edit pdf` | pdf-editing, text-parser | academic-pdf-redaction, marker, pdf, pdf-editing | yes |
| `energy-ac-optimal-power-flow` | `energy ac optimal power flow` | ac-branch-pi-model, casadi-ipopt-nlp, power-flow-data | ac-branch-pi-model, audio-extractor, dc-power-flow, economic-dispatch, power-flow-data | yes |
| `energy-market-pricing` | `energy market pricing` | dc-power-flow, economic-dispatch, locational-marginal-prices, power-flow-data | audio-extractor, energy-calculator, silence-detector | no |
| `energy-unit-commitment` | `energy unit commitment` | milp-solver-workflow, unit-commitment-data-modeling, unit-commitment-operating-rules | audio-extractor, casadi-ipopt-nlp, energy-calculator, unit-commitment-data-modeling, unit-commitment-operating-rules | yes |
| `enterprise-information-search` | `enterprise information search` | enterprise-artifact-search | citation-management, enterprise-artifact-search, ffmpeg-media-info, fuzzy-name-search, gamma-phase-associator | yes |
| `exam-block-sequencing` | `exam block sequencing` | mip-solver-and-solution-audit, ordered-window-sequencing-mip | ordered-window-sequencing-mip, prefix-cache-replay | yes |
| `exceltable-in-ppt` | `exceltable in ppt` | pptx, xlsx@1a801fe1bd5f | (none) | no |
| `exoplanet-detection-period` | `exoplanet detection period` | box-least-squares, exoplanet-workflows, light-curve-preprocessing, lomb-scargle-periodogram, transit-least-squares | energy-calculator, exoplanet-workflows, light-curve-preprocessing, matched-filtering, radar-vital-signs | yes |
| `financial-modeling-qa` | `financial modeling qa` | pdf@0d4af66f868d, xlsx@b1a6bde518fc | obspy-data-api, scip-opt, senior-data-engineer, senior-data-scientist, senior-security | no |
| `fix-build-agentops` | `fix build agentops` | analyze-ci, temporal-python-testing, testing-python, uv-package-manager | d3-visualization, lab-unit-harmonization, lean4-theorem-proving, maven-build-lifecycle, memory-optimization | no |
| `fix-build-google-auto` | `fix build google auto` | maven-build-lifecycle, maven-dependency-management, maven-plugin-configuration | citation-management, d3-visualization, lab-unit-harmonization, lean4-theorem-proving, maven-build-lifecycle | yes |
| `fix-druid-loophole-cve` | `fix druid loophole cve` | jackson-security, senior-java | lab-unit-harmonization, memory-optimization | no |
| `fix-erlang-ssh-cve` | `fix erlang ssh cve` | erlang-concurrency, erlang-distribution, erlang-otp-behaviors, find-bugs, senior-security, ssh-penetration-testing | erlang-concurrency, erlang-distribution, erlang-otp-behaviors, lab-unit-harmonization, memory-optimization | yes |
| `fix-visual-stability` | `fix visual stability` | browser-testing, react-best-practices, web-interface-guidelines | browser-testing, ffmpeg-video-filters, lab-unit-harmonization, memory-optimization, web-interface-guidelines | yes |
| `flink-query` | `flink query` | pdf, senior-data-engineer | hibernate-upgrade, senior-data-engineer | yes |
| `flood-risk-analysis` | `flood risk analysis` | flood-detection, nws-flood-thresholds, usgs-data-download | 13f-analyzer, dc-power-flow, energy-calculator, exoplanet-workflows, ffmpeg-audio-processing | no |
| `glm-lake-mendota` | `glm lake mendota` | glm-basics, glm-calibration, glm-output | glm-basics, glm-calibration, glm-output | yes |
| `gravitational-wave-detection` | `gravitational wave detection` | conditioning, matched-filtering | conditioning, energy-calculator, exoplanet-workflows, matched-filtering, radar-vital-signs | yes |
| `grid-dispatch-operator` | `grid dispatch operator` | dc-power-flow, economic-dispatch, power-flow-data | economic-dispatch, hex-grid-spatial, map-optimization-strategy, parallel-processing, unit-commitment-operating-rules | yes |
| `hvac-control` | `hvac control` | excitation-signal-design, first-order-model-fitting, imc-tuning-rules, safety-interlocks, scipy-curve-fit | attitude-controller-planner, excitation-signal-design, imc-tuning-rules, pid-controller, position-controller-trajectory-planner | yes |
| `invoice-fraud-detection` | `invoice fraud detection` | fuzzy-match, pdf, xlsx | energy-calculator, exoplanet-workflows, matched-filtering, radar-vital-signs, segment-combiner | no |
| `jax-computing-basics` | `jax computing basics` | jax-skills | ac-branch-pi-model, dc-power-flow, economic-dispatch, jax-skills, locational-marginal-prices | yes |
| `jpg-ocr-stat` | `jpg ocr stat` | image-ocr, openai-vision, pdf, video-frame-extraction, xlsx | image-ocr | yes |
| `lab-unit-harmonization` | `lab unit harmonization` | lab-unit-harmonization | casadi-ipopt-nlp, lab-unit-harmonization, temporal-python-testing, unit-commitment-data-modeling, unit-commitment-operating-rules | yes |
| `lake-warming-attribution` | `lake warming attribution` | contribution-analysis, meteorology-driver-classification, pca-decomposition, trend-analysis | glm-basics, meteorology-driver-classification | yes |
| `latex-formula-extraction` | `latex formula extraction` | marker, pdf | enterprise-artifact-search, marker, milp-solver-workflow, ortools-routing-modeling | yes |
| `lean4-proof` | `lean4 proof` | lean4-memories, lean4-theorem-proving | lean4-memories | yes |
| `llm-prefix-cache-replay` | `llm prefix cache replay` | cache-policy-comparison, prefix-cache-replay | cache-policy-comparison, ordered-window-sequencing-mip, prefix-cache-replay, temporal-python-testing | yes |
| `manufacturing-codebook-normalization` | `manufacturing codebook normalization` | manufacturing-failure-reason-codebook-normalization | text-to-speech | no |
| `manufacturing-equipment-maintenance` | `manufacturing equipment maintenance` | reflow-machine-maintenance-guidance, reflow-profile-compliance-toolkit | reflow-machine-maintenance-guidance, safety-interlocks | yes |
| `manufacturing-fjsp-optimization` | `manufacturing fjsp optimization` | fjsp-baseline-repair-with-downtime-and-policy | casadi-ipopt-nlp, economic-dispatch, geospatial-routing-data, grpo, logistics-rules-to-optimization | no |
| `mario-coin-counting` | `mario coin counting` | ffmpeg-keyframe-extraction, image-editing, object-counter | flood-detection | no |
| `mars-clouds-clustering` | `mars clouds clustering` | custom-distance-metrics, parallel-processing, pareto-optimization | custom-distance-metrics, gamma-phase-associator | yes |
| `multilingual-video-dubbing` | `multilingual video dubbing` | ffmpeg-audio-processing, ffmpeg-format-conversion, ffmpeg-media-info, ffmpeg-video-editing, ffmpeg-video-filters, text-to-speech | audio-extractor, ffmpeg-format-conversion, ffmpeg-keyframe-extraction, ffmpeg-video-editing, ffmpeg-video-filters | yes |
| `offer-letter-generator` | `offer letter generator` | docx | economic-dispatch, memory-optimization, power-flow-data, unit-commitment-data-modeling | no |
| `organize-messy-files` | `organize messy files` | docx@d3cfe519dca2, file-organizer, pdf, planning-with-files, pptx@3ad72806f38c | architectural-dxf-extraction, audio-extractor, csv-processing, d3-visualization, energy-calculator | no |
| `paper-anonymizer` | `paper anonymizer` | academic-pdf-redaction, pdf@31b77156e562 | transaction-protocol-reasoning | no |
| `parallel-tfidf-search` | `parallel tfidf search` | memory-optimization, python-parallelization, workload-balancing | citation-management, enterprise-artifact-search, fuzzy-name-search, ortools-routing-modeling, parallel-processing | no |
| `paratransit-routing` | `paratransit routing` | ortools-pickup-delivery-routing, ortools-routing-modeling | geospatial-routing-data, ortools-pickup-delivery-routing, ortools-routing-modeling, routing-subtour-elimination, scip-opt | yes |
| `pddl-airport-planning` | `pddl airport planning` | pddl-skills | attitude-controller-planner, exoplanet-workflows, fjsp-baseline-repair-with-downtime-and-policy, pddl-skills, planning-with-files | yes |
| `pddl-tpp-planning` | `pddl tpp planning` | pddl-skills@3a838435e9e2 | attitude-controller-planner, exoplanet-workflows, fjsp-baseline-repair-with-downtime-and-policy, pddl-skills, planning-with-files | no |
| `pdf-excel-diff` | `pdf excel diff` | pdf, xlsx | academic-pdf-redaction, marker, pdf, pdf-editing | yes |
| `powerlifting-coef-calc` | `powerlifting coef calc` | powerlifting, senior-data-scientist, xlsx | powerlifting | yes |
| `pptx-reference-formatting` | `pptx reference formatting` | pptx@5f3f8d7402fa | citation-management, fuzzy-match, grpo, lab-unit-harmonization, python-scala-syntax-mapping | no |
| `protein-expression-analysis` | `protein expression analysis` | xlsx | 13f-analyzer, dc-power-flow, energy-calculator, exoplanet-workflows, ffmpeg-audio-processing | no |
| `python-scala-translation` | `python scala translation` | python-scala-collections, python-scala-functional, python-scala-idioms, python-scala-libraries, python-scala-oop, python-scala-syntax-mapping | python-scala-collections, python-scala-functional, python-scala-idioms, python-scala-libraries, python-scala-oop | yes |
| `quantum-numerical-simulation` | `quantum numerical simulation` | qutip | csv-processing, glm-basics, glm-calibration, jax-skills, milp-solver-workflow | no |
| `r2r-mpc-control` | `r2r mpc control` | finite-horizon-lqr, integral-action-design, mpc-horizon-tuning, state-space-linearization | attitude-controller-planner, excitation-signal-design, finite-horizon-lqr, imc-tuning-rules, integral-action-design | yes |
| `radar-vital-signs` | `radar vital signs` | radar-signal-processing, radar-vital-signs, vital-sign-extraction | radar-signal-processing, radar-vital-signs, vital-sign-extraction | yes |
| `react-performance-debugging` | `react performance debugging` | browser-testing@ebcae60ef709, react-best-practices@175ee538059c | ac-branch-pi-model, browser-testing, grpo, hibernate-upgrade, jax-skills | no |
| `reserves-at-risk-calc` | `reserves at risk calc` | xlsx | (none) | no |
| `sales-pivot-analysis` | `sales pivot analysis` | pdf@a963d991212a, xlsx@8e5fe91c81dc | 13f-analyzer, dc-power-flow, energy-calculator, exoplanet-workflows, ffmpeg-audio-processing | no |
| `sec-financial-report` | `sec financial report` | 13f-analyzer, fuzzy-name-search | 13f-analyzer | yes |
| `seismic-phase-picking` | `seismic phase picking` | obspy-data-api, obspy-datacenter-client, seisbench-model-api, seismic-picker-selection | ac-branch-pi-model, gamma-phase-associator, pymatgen, seisbench-model-api, seismic-picker-selection | yes |
| `setup-fuzzing-py` | `setup fuzzing py` | discover-important-function, fuzzing-python, setup-env | silence-detector, temporal-python-testing | no |
| `shock-analysis-demand` | `shock analysis demand` | xlsx | 13f-analyzer, dc-power-flow, energy-calculator, exoplanet-workflows, ffmpeg-audio-processing | no |
| `shock-analysis-supply` | `shock analysis supply` | xlsx | 13f-analyzer, dc-power-flow, energy-calculator, exoplanet-workflows, ffmpeg-audio-processing | no |
| `simpo-code-reproduction` | `simpo code reproduction` | nlp-research-repo-package-installment, pdf@ba530d0bb107 | find-bugs, memory-optimization, nlp-research-repo-package-installment, python-parallelization, python-scala-collections | yes |
| `software-dependency-audit` | `software dependency audit` | cvss-score-extraction, trivy-offline-vulnerability-scanning, vulnerability-csv-reporting | find-bugs, maven-dependency-management, obj-exporter, senior-java, spring-boot-migration | no |
| `spring-boot-jakarta-migration` | `spring boot jakarta migration` | hibernate-upgrade, jakarta-namespace, restclient-migration, spring-boot-migration, spring-security-6 | hibernate-upgrade, jakarta-namespace, restclient-migration, senior-java, spring-boot-migration | yes |
| `suricata-custom-exfil` | `suricata custom exfil` | pcap-triage-tshark, suricata-offline-evejson, suricata-rules-basics | custom-distance-metrics, maven-plugin-configuration, obspy-data-api, suricata-offline-evejson, suricata-rules-basics | yes |
| `syzkaller-ppdev-syzlang` | `syzkaller ppdev syzlang` | syz-extract-constants, syzkaller-build-loop, syzlang-ioctl-basics | syz-extract-constants, syzkaller-build-loop, syzlang-ioctl-basics | yes |
| `threejs-structure-parser` | `threejs structure parser` | obj-exporter, threejs | marker, pymatgen | no |
| `threejs-to-obj` | `threejs to obj` | obj-exporter@b6d1bcf98031, threejs | obj-exporter, threejs | yes |
| `tictoc-unnecessary-abort-detection` | `tictoc unnecessary abort detection` | transaction-concurrency-control-foundations, transaction-protocol-reasoning, transaction-trace-analysis | energy-calculator, exoplanet-workflows, matched-filtering, radar-vital-signs, segment-combiner | no |
| `travel-planning` | `travel planning` | search-accommodations, search-attractions, search-cities, search-driving-distance, search-flights, search-restaurants | attitude-controller-planner, exoplanet-workflows, fjsp-baseline-repair-with-downtime-and-policy, geospatial-routing-data, pddl-skills | no |
| `video-silence-remover` | `video silence remover` | audio-extractor, energy-calculator, pause-detector, report-generator, segment-combiner, silence-detector, video-processor | audio-extractor, energy-calculator, ffmpeg-format-conversion, ffmpeg-keyframe-extraction, silence-detector | yes |
| `weighted-gdp-calc` | `weighted gdp calc` | xlsx | (none) | no |
| `xlsx-recover-data` | `xlsx recover data` | data-reconciliation, xlsx | 13f-analyzer, conditioning, csv-processing, cvss-score-extraction, xlsx | yes |

## mechanical, k=10 — query rule: task_id with hyphens replaced by spaces

| task_id | query as sent | oracle | provisioned | hit |
|---|---|---|---|---|
| `3d-scan-calc` | `3d scan calc` | mesh-analysis | mesh-analysis, obj-exporter, stepinfo-3d | yes |
| `ada-bathroom-plan-repair` | `ada bathroom plan repair` | ada-plan-view-accessibility, architectural-dxf-extraction, geometric-layout-repair | ada-plan-view-accessibility, architectural-dxf-extraction, fjsp-baseline-repair-with-downtime-and-policy, lean4-theorem-proving, pddl-skills, position-controller-trajectory-planner | yes |
| `adaptive-cruise-control` | `adaptive cruise control` | csv-processing, pid-controller, simulation-metrics, vehicle-dynamics, yaml-config | attitude-controller-planner, excitation-signal-design, imc-tuning-rules, pid-controller, position-controller-trajectory-planner, python-scala-syntax-mapping, safety-interlocks, simulation-metrics, state-space-linearization, vehicle-dynamics | yes |
| `azure-bgp-oscillation-route-leak` | `azure bgp oscillation route leak` | azure-bgp | azure-bgp, geospatial-routing-data, ortools-routing-modeling, routing-subtour-elimination, search-flights | yes |
| `bike-rebalance` | `bike rebalance` | geospatial-routing-data, logistics-rules-to-optimization, routing-subtour-elimination, scip-opt | (none) | no |
| `citation-check` | `citation check` | citation-management | browser-testing, citation-management, output-validation | yes |
| `civ6-adjacency-optimizer` | `civ6 adjacency optimizer` | civ6lib, hex-grid-spatial, map-optimization-strategy, sqlite-map-parser | civ6lib | yes |
| `court-form-filling` | `court form filling` | pdf | pdf | yes |
| `crystallographic-wyckoff-position-analysis` | `crystallographic wyckoff position analysis` | pymatgen, sympy | 13f-analyzer, dc-power-flow, energy-calculator, exoplanet-workflows, ffmpeg-audio-processing, ffmpeg-keyframe-extraction, lab-unit-harmonization, light-curve-preprocessing, locational-marginal-prices, meteorology-driver-classification | no |
| `dapt-intrusion-detection` | `dapt intrusion detection` | pcap-analysis, threat-detection | energy-calculator, exoplanet-workflows, matched-filtering, radar-vital-signs, segment-combiner, seisbench-model-api, seismic-picker-selection, threat-detection | yes |
| `data-to-d3` | `data to d3` | d3-visualization | 13f-analyzer, conditioning, csv-processing, cvss-score-extraction, d3-visualization, energy-calculator, exoplanet-workflows, first-order-model-fitting, flood-detection, fuzzy-match | yes |
| `debug-trl-grpo` | `debug trl grpo` | grpo, rl-post-training, trl | grpo, radar-signal-processing, rl-post-training, trl | yes |
| `dialogue-parser` | `dialogue parser` | dialogue-graph | dialogue-graph | yes |
| `drone-planning-control` | `drone planning control` | attitude-controller-planner, flight-plan-parser, motor-model-dynamics, plot-quadrotor, position-controller-trajectory-planner, stepinfo-3d | attitude-controller-planner, excitation-signal-design, exoplanet-workflows, fjsp-baseline-repair-with-downtime-and-policy, flight-plan-parser, imc-tuning-rules, pddl-skills, pid-controller, planning-with-files, position-controller-trajectory-planner | yes |
| `dynamic-object-aware-egomotion` | `dynamic object aware egomotion` | dyn-object-masks, egomotion-estimation, output-validation, sampling-and-indexing | dyn-object-masks, finite-horizon-lqr, first-order-model-fitting, jax-skills, memory-optimization, object-counter, pause-detector, python-scala-oop, seisbench-model-api, transaction-trace-analysis | yes |
| `earthquake-phase-association` | `earthquake phase association` | gamma-phase-associator, obspy-data-api, seisbench-model-api, seismic-picker-selection | ac-branch-pi-model, gamma-phase-associator, geospatial-analysis, obspy-datacenter-client, pymatgen, radar-signal-processing, radar-vital-signs, seisbench-model-api, seismic-picker-selection, vital-sign-extraction | yes |
| `earthquake-plate-calculation` | `earthquake plate calculation` | geospatial-analysis | audio-extractor, gamma-phase-associator, geospatial-analysis, obspy-datacenter-client, seisbench-model-api, seismic-picker-selection | yes |
| `econ-detrending-correlation` | `econ detrending correlation` | timeseries-detrending | qutip, timeseries-detrending | yes |
| `edit-pdf` | `edit pdf` | pdf-editing, text-parser | academic-pdf-redaction, marker, pdf, pdf-editing | yes |
| `energy-ac-optimal-power-flow` | `energy ac optimal power flow` | ac-branch-pi-model, casadi-ipopt-nlp, power-flow-data | ac-branch-pi-model, audio-extractor, casadi-ipopt-nlp, conditioning, dc-power-flow, economic-dispatch, egomotion-estimation, energy-calculator, fjsp-baseline-repair-with-downtime-and-policy, power-flow-data | yes |
| `energy-market-pricing` | `energy market pricing` | dc-power-flow, economic-dispatch, locational-marginal-prices, power-flow-data | audio-extractor, energy-calculator, silence-detector | no |
| `energy-unit-commitment` | `energy unit commitment` | milp-solver-workflow, unit-commitment-data-modeling, unit-commitment-operating-rules | audio-extractor, casadi-ipopt-nlp, energy-calculator, silence-detector, temporal-python-testing, unit-commitment-data-modeling, unit-commitment-operating-rules | yes |
| `enterprise-information-search` | `enterprise information search` | enterprise-artifact-search | citation-management, enterprise-artifact-search, ffmpeg-media-info, fuzzy-name-search, gamma-phase-associator, ortools-routing-modeling, parallel-processing, search-flights, senior-java | yes |
| `exam-block-sequencing` | `exam block sequencing` | mip-solver-and-solution-audit, ordered-window-sequencing-mip | ordered-window-sequencing-mip, prefix-cache-replay | yes |
| `exceltable-in-ppt` | `exceltable in ppt` | pptx, xlsx@1a801fe1bd5f | (none) | no |
| `exoplanet-detection-period` | `exoplanet detection period` | box-least-squares, exoplanet-workflows, light-curve-preprocessing, lomb-scargle-periodogram, transit-least-squares | energy-calculator, exoplanet-workflows, light-curve-preprocessing, matched-filtering, radar-vital-signs, segment-combiner, seisbench-model-api, seismic-picker-selection, threat-detection, transit-least-squares | yes |
| `financial-modeling-qa` | `financial modeling qa` | pdf@0d4af66f868d, xlsx@b1a6bde518fc | obspy-data-api, scip-opt, senior-data-engineer, senior-data-scientist, senior-security | no |
| `fix-build-agentops` | `fix build agentops` | analyze-ci, temporal-python-testing, testing-python, uv-package-manager | d3-visualization, lab-unit-harmonization, lean4-theorem-proving, maven-build-lifecycle, memory-optimization, ortools-routing-modeling, syzkaller-build-loop | no |
| `fix-build-google-auto` | `fix build google auto` | maven-build-lifecycle, maven-dependency-management, maven-plugin-configuration | citation-management, d3-visualization, lab-unit-harmonization, lean4-theorem-proving, maven-build-lifecycle, memory-optimization, ortools-routing-modeling, syzkaller-build-loop | yes |
| `fix-druid-loophole-cve` | `fix druid loophole cve` | jackson-security, senior-java | lab-unit-harmonization, memory-optimization | no |
| `fix-erlang-ssh-cve` | `fix erlang ssh cve` | erlang-concurrency, erlang-distribution, erlang-otp-behaviors, find-bugs, senior-security, ssh-penetration-testing | erlang-concurrency, erlang-distribution, erlang-otp-behaviors, lab-unit-harmonization, memory-optimization, ssh-penetration-testing | yes |
| `fix-visual-stability` | `fix visual stability` | browser-testing, react-best-practices, web-interface-guidelines | browser-testing, ffmpeg-video-filters, lab-unit-harmonization, memory-optimization, react-best-practices, rl-post-training, web-interface-guidelines | yes |
| `flink-query` | `flink query` | pdf, senior-data-engineer | hibernate-upgrade, senior-data-engineer | yes |
| `flood-risk-analysis` | `flood risk analysis` | flood-detection, nws-flood-thresholds, usgs-data-download | 13f-analyzer, dc-power-flow, energy-calculator, exoplanet-workflows, ffmpeg-audio-processing, ffmpeg-keyframe-extraction, flood-detection, lab-unit-harmonization, light-curve-preprocessing, locational-marginal-prices | yes |
| `glm-lake-mendota` | `glm lake mendota` | glm-basics, glm-calibration, glm-output | glm-basics, glm-calibration, glm-output | yes |
| `gravitational-wave-detection` | `gravitational wave detection` | conditioning, matched-filtering | conditioning, energy-calculator, exoplanet-workflows, matched-filtering, radar-vital-signs, segment-combiner, seisbench-model-api, seismic-picker-selection, threat-detection | yes |
| `grid-dispatch-operator` | `grid dispatch operator` | dc-power-flow, economic-dispatch, power-flow-data | economic-dispatch, hex-grid-spatial, map-optimization-strategy, parallel-processing, unit-commitment-operating-rules | yes |
| `hvac-control` | `hvac control` | excitation-signal-design, first-order-model-fitting, imc-tuning-rules, safety-interlocks, scipy-curve-fit | attitude-controller-planner, excitation-signal-design, imc-tuning-rules, pid-controller, position-controller-trajectory-planner, python-scala-syntax-mapping, safety-interlocks, simulation-metrics, state-space-linearization, transaction-concurrency-control-foundations | yes |
| `invoice-fraud-detection` | `invoice fraud detection` | fuzzy-match, pdf, xlsx | energy-calculator, exoplanet-workflows, matched-filtering, radar-vital-signs, segment-combiner, seisbench-model-api, seismic-picker-selection, threat-detection | no |
| `jax-computing-basics` | `jax computing basics` | jax-skills | ac-branch-pi-model, dc-power-flow, economic-dispatch, jax-skills, locational-marginal-prices, pareto-optimization, pcap-analysis, qutip, stepinfo-3d, syzlang-ioctl-basics | yes |
| `jpg-ocr-stat` | `jpg ocr stat` | image-ocr, openai-vision, pdf, video-frame-extraction, xlsx | image-ocr | yes |
| `lab-unit-harmonization` | `lab unit harmonization` | lab-unit-harmonization | casadi-ipopt-nlp, lab-unit-harmonization, temporal-python-testing, unit-commitment-data-modeling, unit-commitment-operating-rules | yes |
| `lake-warming-attribution` | `lake warming attribution` | contribution-analysis, meteorology-driver-classification, pca-decomposition, trend-analysis | glm-basics, meteorology-driver-classification | yes |
| `latex-formula-extraction` | `latex formula extraction` | marker, pdf | enterprise-artifact-search, marker, milp-solver-workflow, ortools-routing-modeling | yes |
| `lean4-proof` | `lean4 proof` | lean4-memories, lean4-theorem-proving | lean4-memories | yes |
| `llm-prefix-cache-replay` | `llm prefix cache replay` | cache-policy-comparison, prefix-cache-replay | cache-policy-comparison, ordered-window-sequencing-mip, prefix-cache-replay, temporal-python-testing | yes |
| `manufacturing-codebook-normalization` | `manufacturing codebook normalization` | manufacturing-failure-reason-codebook-normalization | text-to-speech | no |
| `manufacturing-equipment-maintenance` | `manufacturing equipment maintenance` | reflow-machine-maintenance-guidance, reflow-profile-compliance-toolkit | reflow-machine-maintenance-guidance, safety-interlocks | yes |
| `manufacturing-fjsp-optimization` | `manufacturing fjsp optimization` | fjsp-baseline-repair-with-downtime-and-policy | casadi-ipopt-nlp, economic-dispatch, geospatial-routing-data, grpo, logistics-rules-to-optimization, map-optimization-strategy, milp-solver-workflow, mip-solver-and-solution-audit, pareto-optimization, scip-opt | no |
| `mario-coin-counting` | `mario coin counting` | ffmpeg-keyframe-extraction, image-editing, object-counter | flood-detection | no |
| `mars-clouds-clustering` | `mars clouds clustering` | custom-distance-metrics, parallel-processing, pareto-optimization | custom-distance-metrics, gamma-phase-associator | yes |
| `multilingual-video-dubbing` | `multilingual video dubbing` | ffmpeg-audio-processing, ffmpeg-format-conversion, ffmpeg-media-info, ffmpeg-video-editing, ffmpeg-video-filters, text-to-speech | audio-extractor, ffmpeg-format-conversion, ffmpeg-keyframe-extraction, ffmpeg-video-editing, ffmpeg-video-filters, report-generator, sampling-and-indexing, segment-combiner, silence-detector, video-frame-extraction | yes |
| `offer-letter-generator` | `offer letter generator` | docx | economic-dispatch, memory-optimization, power-flow-data, unit-commitment-data-modeling | no |
| `organize-messy-files` | `organize messy files` | docx@d3cfe519dca2, file-organizer, pdf, planning-with-files, pptx@3ad72806f38c | architectural-dxf-extraction, audio-extractor, csv-processing, d3-visualization, energy-calculator, ffmpeg-format-conversion, ffmpeg-keyframe-extraction, ffmpeg-video-editing, file-organizer, glm-basics | yes |
| `paper-anonymizer` | `paper anonymizer` | academic-pdf-redaction, pdf@31b77156e562 | transaction-protocol-reasoning | no |
| `parallel-tfidf-search` | `parallel tfidf search` | memory-optimization, python-parallelization, workload-balancing | citation-management, enterprise-artifact-search, fuzzy-name-search, ortools-routing-modeling, parallel-processing, python-parallelization, search-flights, workload-balancing | yes |
| `paratransit-routing` | `paratransit routing` | ortools-pickup-delivery-routing, ortools-routing-modeling | geospatial-routing-data, ortools-pickup-delivery-routing, ortools-routing-modeling, routing-subtour-elimination, scip-opt | yes |
| `pddl-airport-planning` | `pddl airport planning` | pddl-skills | attitude-controller-planner, exoplanet-workflows, fjsp-baseline-repair-with-downtime-and-policy, pddl-skills, planning-with-files, position-controller-trajectory-planner | yes |
| `pddl-tpp-planning` | `pddl tpp planning` | pddl-skills@3a838435e9e2 | attitude-controller-planner, exoplanet-workflows, fjsp-baseline-repair-with-downtime-and-policy, pddl-skills, planning-with-files, position-controller-trajectory-planner | no |
| `pdf-excel-diff` | `pdf excel diff` | pdf, xlsx | academic-pdf-redaction, marker, pdf, pdf-editing | yes |
| `powerlifting-coef-calc` | `powerlifting coef calc` | powerlifting, senior-data-scientist, xlsx | powerlifting | yes |
| `pptx-reference-formatting` | `pptx reference formatting` | pptx@5f3f8d7402fa | citation-management, fuzzy-match, grpo, lab-unit-harmonization, pptx, python-scala-syntax-mapping, trl, vulnerability-csv-reporting, xlsx | no |
| `protein-expression-analysis` | `protein expression analysis` | xlsx | 13f-analyzer, dc-power-flow, energy-calculator, exoplanet-workflows, ffmpeg-audio-processing, ffmpeg-keyframe-extraction, lab-unit-harmonization, light-curve-preprocessing, locational-marginal-prices, meteorology-driver-classification | no |
| `python-scala-translation` | `python scala translation` | python-scala-collections, python-scala-functional, python-scala-idioms, python-scala-libraries, python-scala-oop, python-scala-syntax-mapping | docx, fuzzing-python, gamma-phase-associator, image-ocr, python-scala-collections, python-scala-functional, python-scala-idioms, python-scala-libraries, python-scala-oop, python-scala-syntax-mapping | yes |
| `quantum-numerical-simulation` | `quantum numerical simulation` | qutip | csv-processing, glm-basics, glm-calibration, jax-skills, milp-solver-workflow, plot-quadrotor, qutip, rl-post-training, simulation-metrics, sympy | yes |
| `r2r-mpc-control` | `r2r mpc control` | finite-horizon-lqr, integral-action-design, mpc-horizon-tuning, state-space-linearization | attitude-controller-planner, excitation-signal-design, finite-horizon-lqr, imc-tuning-rules, integral-action-design, mpc-horizon-tuning, pid-controller, position-controller-trajectory-planner, python-scala-syntax-mapping, safety-interlocks | yes |
| `radar-vital-signs` | `radar vital signs` | radar-signal-processing, radar-vital-signs, vital-sign-extraction | radar-signal-processing, radar-vital-signs, vital-sign-extraction | yes |
| `react-performance-debugging` | `react performance debugging` | browser-testing@ebcae60ef709, react-best-practices@175ee538059c | ac-branch-pi-model, browser-testing, grpo, hibernate-upgrade, jax-skills, milp-solver-workflow, powerlifting, python-parallelization, react-best-practices, rl-post-training | no |
| `reserves-at-risk-calc` | `reserves at risk calc` | xlsx | (none) | no |
| `sales-pivot-analysis` | `sales pivot analysis` | pdf@a963d991212a, xlsx@8e5fe91c81dc | 13f-analyzer, dc-power-flow, energy-calculator, exoplanet-workflows, ffmpeg-audio-processing, ffmpeg-keyframe-extraction, lab-unit-harmonization, light-curve-preprocessing, locational-marginal-prices, meteorology-driver-classification | no |
| `sec-financial-report` | `sec financial report` | 13f-analyzer, fuzzy-name-search | 13f-analyzer | yes |
| `seismic-phase-picking` | `seismic phase picking` | obspy-data-api, obspy-datacenter-client, seisbench-model-api, seismic-picker-selection | ac-branch-pi-model, gamma-phase-associator, pymatgen, radar-signal-processing, radar-vital-signs, seisbench-model-api, seismic-picker-selection, vital-sign-extraction | yes |
| `setup-fuzzing-py` | `setup fuzzing py` | discover-important-function, fuzzing-python, setup-env | silence-detector, temporal-python-testing | no |
| `shock-analysis-demand` | `shock analysis demand` | xlsx | 13f-analyzer, dc-power-flow, energy-calculator, exoplanet-workflows, ffmpeg-audio-processing, ffmpeg-keyframe-extraction, lab-unit-harmonization, light-curve-preprocessing, locational-marginal-prices, meteorology-driver-classification | no |
| `shock-analysis-supply` | `shock analysis supply` | xlsx | 13f-analyzer, dc-power-flow, energy-calculator, exoplanet-workflows, ffmpeg-audio-processing, ffmpeg-keyframe-extraction, lab-unit-harmonization, light-curve-preprocessing, locational-marginal-prices, meteorology-driver-classification | no |
| `simpo-code-reproduction` | `simpo code reproduction` | nlp-research-repo-package-installment, pdf@ba530d0bb107 | find-bugs, memory-optimization, nlp-research-repo-package-installment, python-parallelization, python-scala-collections, python-scala-functional, python-scala-idioms, python-scala-libraries, python-scala-oop, python-scala-syntax-mapping | yes |
| `software-dependency-audit` | `software dependency audit` | cvss-score-extraction, trivy-offline-vulnerability-scanning, vulnerability-csv-reporting | find-bugs, maven-dependency-management, obj-exporter, senior-java, spring-boot-migration, ssh-penetration-testing, trivy-offline-vulnerability-scanning, uv-package-manager, vulnerability-csv-reporting | yes |
| `spring-boot-jakarta-migration` | `spring boot jakarta migration` | hibernate-upgrade, jakarta-namespace, restclient-migration, spring-boot-migration, spring-security-6 | hibernate-upgrade, jakarta-namespace, restclient-migration, senior-java, spring-boot-migration, spring-security-6 | yes |
| `suricata-custom-exfil` | `suricata custom exfil` | pcap-triage-tshark, suricata-offline-evejson, suricata-rules-basics | custom-distance-metrics, maven-plugin-configuration, obspy-data-api, suricata-offline-evejson, suricata-rules-basics | yes |
| `syzkaller-ppdev-syzlang` | `syzkaller ppdev syzlang` | syz-extract-constants, syzkaller-build-loop, syzlang-ioctl-basics | syz-extract-constants, syzkaller-build-loop, syzlang-ioctl-basics | yes |
| `threejs-structure-parser` | `threejs structure parser` | obj-exporter, threejs | marker, pymatgen | no |
| `threejs-to-obj` | `threejs to obj` | obj-exporter@b6d1bcf98031, threejs | obj-exporter, threejs | yes |
| `tictoc-unnecessary-abort-detection` | `tictoc unnecessary abort detection` | transaction-concurrency-control-foundations, transaction-protocol-reasoning, transaction-trace-analysis | energy-calculator, exoplanet-workflows, matched-filtering, radar-vital-signs, segment-combiner, seisbench-model-api, seismic-picker-selection, threat-detection, transaction-concurrency-control-foundations, transaction-trace-analysis | yes |
| `travel-planning` | `travel planning` | search-accommodations, search-attractions, search-cities, search-driving-distance, search-flights, search-restaurants | attitude-controller-planner, exoplanet-workflows, fjsp-baseline-repair-with-downtime-and-policy, geospatial-routing-data, pddl-skills, planning-with-files, position-controller-trajectory-planner, search-driving-distance | yes |
| `video-silence-remover` | `video silence remover` | audio-extractor, energy-calculator, pause-detector, report-generator, segment-combiner, silence-detector, video-processor | audio-extractor, energy-calculator, ffmpeg-format-conversion, ffmpeg-keyframe-extraction, ffmpeg-video-editing, ffmpeg-video-filters, pause-detector, report-generator, sampling-and-indexing, silence-detector | yes |
| `weighted-gdp-calc` | `weighted gdp calc` | xlsx | (none) | no |
| `xlsx-recover-data` | `xlsx recover data` | data-reconciliation, xlsx | 13f-analyzer, conditioning, csv-processing, cvss-score-extraction, d3-visualization, data-reconciliation, energy-calculator, exoplanet-workflows, first-order-model-fitting, xlsx | yes |

## handwritten probe, k=3 — queries authored locally

These were written by the same author who knew the oracle. That is the
bias this file exists to make visible.

| arm | task_id | query as sent | oracle | provisioned | hit |
|---|---|---|---|---|---|
| cued | `court-form-filling` | `Fill out a court form PDF with the provided applicant details.` | pdf | academic-pdf-redaction, ffmpeg-keyframe-extraction, pdf | yes |
| cued | `dialogue-parser` | `Parse a dialogue transcript into a structured dialogue graph.` | dialogue-graph | dialogue-graph, enterprise-artifact-search, sqlite-map-parser | yes |
| cued | `offer-letter-generator` | `Generate an offer letter as a Word docx document.` | docx | citation-management, d3-visualization, docx | yes |
| cued | `powerlifting-coef-calc` | `Calculate powerlifting coefficients into an xlsx spreadsheet.` | powerlifting, senior-data-scientist, xlsx | contribution-analysis, data-reconciliation, xlsx | yes |
| cued | `fix-build-agentops` | `Fix the failing CI build for this Python project.` | analyze-ci, temporal-python-testing, testing-python, uv-package-manager | fuzzing-python, memory-optimization, setup-env | no |
| cued | `fix-build-google-auto` | `Fix the failing Maven build and its dependency configuration.` | maven-build-lifecycle, maven-dependency-management, maven-plugin-configuration | d3-visualization, maven-build-lifecycle, maven-dependency-management | yes |
| uncued | `court-form-filling` | `Complete the applicant fields on the official legal document and save it.` | pdf | ac-branch-pi-model, docx, marker | no |
| uncued | `dialogue-parser` | `Turn the conversation transcript into a structured representation of who said what to whom.` | dialogue-graph | enterprise-artifact-search, flight-plan-parser, report-generator | no |
| uncued | `offer-letter-generator` | `Produce a formatted employment offer letter for the candidate.` | docx | citation-management, radar-signal-processing | no |
| uncued | `powerlifting-coef-calc` | `Work out the strength scores for each lifter and lay them out in a table.` | powerlifting, senior-data-scientist, xlsx | browser-testing, contribution-analysis, cvss-score-extraction | no |
| uncued | `fix-build-agentops` | `The automated pipeline is failing on this repository. Diagnose and repair it.` | analyze-ci, temporal-python-testing, testing-python, uv-package-manager | exoplanet-workflows, fjsp-baseline-repair-with-downtime-and-policy, grpo | no |
| uncued | `fix-build-google-auto` | `The Java project will not compile because of its dependency setup. Repair it.` | maven-build-lifecycle, maven-dependency-management, maven-plugin-configuration | maven-dependency-management, senior-java, spring-boot-migration | yes |
