package metrics

import "sync"

type Metrics struct {
	mu sync.Mutex

	AgentRunsTotal int

	AgentStepsTotal      map[string]map[string]int
	AgentDowngradesTotal map[string]int

	AgentHardStopsTotal int

	AgentCostTotal float64
	AgentCostSaved float64

	SLAViolationsPrevented int
}

func New() *Metrics {
	return &Metrics{
		AgentStepsTotal:      make(map[string]map[string]int),
		AgentDowngradesTotal: make(map[string]int),
	}
}

func (m *Metrics) IncAgentRun() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.AgentRunsTotal++
}

func (m *Metrics) IncStep(step, tier string) {
	m.mu.Lock()
	defer m.mu.Unlock()

	if _, ok := m.AgentStepsTotal[step]; !ok {
		m.AgentStepsTotal[step] = make(map[string]int)
	}
	m.AgentStepsTotal[step][tier]++
}

func (m *Metrics) IncDowngrade(reason string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.AgentDowngradesTotal[reason]++
}

func (m *Metrics) IncHardStop() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.AgentHardStopsTotal++
}

func (m *Metrics) AddCost(cost float64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.AgentCostTotal += cost
}

func (m *Metrics) AddCostSaved(saved float64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.AgentCostSaved += saved
}

func (m *Metrics) IncSLAPrevented() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.SLAViolationsPrevented++
}
