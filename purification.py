"""Code for BBPSSW entanglement purification.

This module defines code to support the BBPSSW protocol for entanglement purification.
Success results are pre-determined based on network parameters.
Also defined is the message type used by the BBPSSW code.
"""

from sequence.components.memory import Memory
from sequence.topology.node import Node
from sequence.entanglement_management.purification.bbpssw_protocol import BBPSSWMessage, BBPSSWMsgType, BBPSSWProtocol
from sequence.entanglement_management.purification.bbpssw_bds import BBPSSW_BDS
from sequence.entanglement_management.purification.bbpssw_circuit import BBPSSWCircuit
from sequence.utils import log


# custom purification types
BBPSSW_BDS_ADAPTIVE = 'bbpssw_bds_adaptive'
BBPSSW_CIRCUIT_ADAPTIVE = 'bbpssw_circuit_adaptive'


@BBPSSWProtocol.register(BBPSSW_CIRCUIT_ADAPTIVE)
class BBPSSW_Circuit_Adaptive(BBPSSWCircuit):
    def __init__(self, owner: Node, name: str, kept_memo: Memory, meas_memo: Memory):
        super().__init__(owner, name, kept_memo, meas_memo)
        self.protocol_type = BBPSSW_CIRCUIT_ADAPTIVE

    def received_message(self, src: str, msg: BBPSSWMessage) -> None:
        # check the status of entanglement
        if self.meas_memo.entangled_memory['node_id'] is None or self.kept_memo.entangled_memory['node_id'] is None:
            log.logger.info(f'No entanglement for {self.meas_memo} or {self.kept_memo}.')
            # when the AC Protocol expires, the purification protocol on the primary node will get removed, but the purification protocol on the non-primary node is still there
            self.owner.protocols.remove(self)
            return

        super().received_message(src, msg)


@BBPSSWProtocol.register(BBPSSW_BDS_ADAPTIVE)
class BBPSSW_BDS_Adaptive(BBPSSW_BDS):
    """Purification protocol instance.

    This class provides an implementation of the BBPSSW purification protocol.
    It should be instantiated on a quantum router node.
    This version of the BBPSSW uses the Bell Diagonal State formalism

    Attributes:
        owner (QuantumRouter): node that protocol instance is attached to.
        name (str): label for protocol instance.
        kept_memo: memory to be purified by the protocol (should already be entangled).
        meas_memo: memory to measure and discart (should already be entangled).
        meas_res (int): measurement result from circuit.
        remote_node_name (str): name of other node.
        remote_protocol_name (str): name of other protocol.
        remote_memories (List[str]): name of remote memories.
        is_twirled (bool): whether we twirl the input and output BDS. True: BBPSSW, False: DEJMPS. (default True)
    """

    def __init__(self, owner: Node, name: str, kept_memo: Memory, meas_memo: Memory, is_twirled=True):
        """Constructor for purification protocol.

        args:
            owner (Node): Node the protocol of which the protocol is attached.
            name (str): Name of protocol instance.
            kept_memo (Memory): Memory to keep and improve the fidelity.
            meas_memo (Memory): Memory to measure and discard.
            is_twirled (bool): Whether we twirl the input and output BDS. True: BBPSSW, False: DEJMPS. (default True)
        """
        super().__init__(owner, name, kept_memo, meas_memo, is_twirled)
        self.protocol_type = BBPSSW_BDS_ADAPTIVE

    def received_message(self, src: str, msg: BBPSSWMessage) -> None:
        """Method to receive messages.

        Args:
            src (str): name of node that sent the message.
            msg (BBPSSW message): message received.

        Side Effects:
            Will call `update_resource_manager` method.
        """

        # check the status of entanglement
        if self.meas_memo.entangled_memory['node_id'] is None or self.kept_memo.entangled_memory['node_id'] is None:
            log.logger.info(f'No entanglement for {self.meas_memo} or {self.kept_memo}.')
            # when the AC Protocol expires, the purification protocol on the primary node will get removed, but the purification protocol on the non-primary node is still there
            self.owner.protocols.remove(self)
            return

        super().received_message(src, msg)
