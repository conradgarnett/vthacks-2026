import { WorldSim, ATTACKERS } from '@sense/world-sim';

/** Creates the local dev CA and log keys (gitignored) and prints the simulated world. Nothing here is real. */
const keyDir = process.env.SENSE_KEY_DIR ?? '.sense/keys';
const world = await WorldSim.create({ keyDir });
console.log(`Local dev CA and transparency-log keys are in ${keyDir}/ (gitignored, never commit them).`);
console.log('\nSIMULATED WORLD, one hostname per agent:');
for (const [fqdn, agent] of [...world.agents.entries()].sort()) {
  const card = agent.identity.card();
  console.log(`  ${fqdn.padEnd(24)} ${card.capabilities.map((c) => c.id).join(', ')}`);
}
console.log(`\nTransparency log size: ${world.registry.log.size} registrations.`);
console.log('\nAttacker agents (inactive until activated; used by the tests and the attacker scene):');
for (const [id, fqdn] of Object.entries(ATTACKERS)) console.log(`  ${id.padEnd(12)} ${fqdn}`);
