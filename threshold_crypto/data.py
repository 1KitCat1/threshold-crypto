import base64
import collections
import json
from typing import Iterable

from Crypto.PublicKey import ECC


class ThresholdCryptoError(Exception):
    pass


class ThresholdDataClass:
    """ Baseclass for ThresholdCrypto data classes. """
    BASE64_MAGIC = "BASE64|"
    CURVE_MAGIC = "ECURVE|"

    def __init__(self):
        raise NotImplementedError("Implement __init__ in subclass when using ThresholdDataClass")

    def to_json(self):
        """ Create json representation of object. Some special cases are already handled here. """
        dict = self.__dict__.copy()

        for k in dict:
            # special handling of bytes
            if isinstance(dict[k], bytes):
                dict[k] = self.BASE64_MAGIC + base64.b64encode(dict[k]).decode('ascii')

            # special handling of curve parameters
            if isinstance(dict[k], CurveParameters):
                dict[k] = self.CURVE_MAGIC + dict[k]._name

            # special handling of curve points
            if isinstance(dict[k], ECC.EccPoint):
                p = dict[k]
                dict[k] = {
                    "x": int(p.x),
                    "y": int(p.y),
                    "curve": p._curve_name,
                }

        return json.dumps(dict)

    @classmethod
    def from_json(cls, json_str: str):
        """ Create object from json representation. Some special cases are already handled here. """
        dict = json.loads(json_str)

        for k in dict:
            # special handling of bytes
            if isinstance(dict[k], str) and dict[k].startswith(cls.BASE64_MAGIC):
                dict[k] = base64.b64decode(dict[k][len(cls.BASE64_MAGIC):].encode('ascii'))

            # special handling of curve parameters
            if isinstance(dict[k], str) and dict[k].startswith(cls.CURVE_MAGIC):
                dict[k] = CurveParameters(curve_name=dict[k][len(cls.CURVE_MAGIC):])

            # special handling of curve points
            if isinstance(dict[k], collections.Mapping) and "x" in dict[k] and "y" in dict[k] and "curve" in dict[k]:
                dict[k] = ECC.EccPoint(**dict[k])

        return cls(**dict)


class ThresholdParameters(ThresholdDataClass):
    """
    Contains the parameters used for the threshold scheme:
    - t: number of share owners required to decrypt a message
    - n: number of share owners involved

    In other words:
    At least t out of overall n share owners must participate to decrypt an encrypted message.
    """

    def __init__(self, t: int, n: int):
        """
        Construct threshold parameter. Required:
        0 < t <= n

        :param t:  number of share owners required for decryption
        :param n: overall number of share owners
        """
        if t > n:
            raise ThresholdCryptoError('threshold parameter t must be smaller than n')
        if t <= 0:
            raise ThresholdCryptoError('threshold parameter t must be greater than 0')

        self.t = t
        self.n = n

    def __eq__(self, other):
        return (isinstance(other, self.__class__) and
                self.t == other.t and
                self.n == other.n)

    def __str__(self):
        return 'ThresholdParameters ({}, {})'.format(self.t, self.n)


class CurveParameters(ThresholdDataClass):
    """
    Contains the curve parameters the scheme uses. Since PyCryptodome is used, only curves present there are available:
    https://pycryptodome.readthedocs.io/en/latest/src/public_key/ecc.html
    """
    DEFAULT_CURVE = 'P-256'

    def __init__(self, curve_name: str = DEFAULT_CURVE):
        """
        Construct the curve from a given curve name (according to curves present in PyCryptodome).

        :param curve_name:
        """
        if curve_name not in ECC._curves:
            raise ThresholdCryptoError('Unsupported curve: ' + curve_name)

        self._name = curve_name
        self._curve = ECC._curves[curve_name]
        self.P = ECC.EccPoint(x=self._curve.Gx, y=self._curve.Gy, curve=curve_name)

    @property
    def order(self):
        return int(self._curve.order)

    def to_json(self):
        return json.dumps({'curve_name': self._name})

    def __eq__(self, other):
        return (isinstance(other, self.__class__) and
                self._curve == other._curve)

    def __str__(self):
        return "Curve {} of order {} with generator point P = {}".format(self._name, self.order, self.P)


class PublicKey(ThresholdDataClass):
    """
    The public key point Q linked to the (implicit) secret key d of the scheme.
    """

    def __init__(self, Q: ECC.EccPoint, curve_params: CurveParameters = CurveParameters()):
        """
        Construct the public key.

        :param Q: the public key point Q = dP
        :param curve_params: the curve parameters used for constructing the key.
        """
        self.Q = Q
        self.curve_params = curve_params

    def __eq__(self, other):
        return (isinstance(other, self.__class__) and
                self.curve_params == other.curve_params and
                self.Q == other.Q)

    def __str__(self):
        return 'Public key point Q = {} (on curve {})'.format(self.Q, self.curve_params._name)


class KeyShare(ThresholdDataClass):
    """
    A share (x_i, y_i) of the secret key d for share owner i.
    y_i is the evaluated polynom value of x_i in shamirs secret sharing.
    """

    def __init__(self, x: int, y: int, curve_params: CurveParameters):
        """
        Construct a share of the private key d.

        :param x: the x value of the share
        :param y: the y value of the share
        :param curve_params: the curve parameters used
        """
        self.x = x
        self.y = y
        self.curve_params = curve_params

    def __eq__(self, other):
        return (isinstance(other, self.__class__) and
                self.curve_params == other.curve_params and
                self.x == other.x and
                self.y == other.y)

    def __str__(self):
        return 'KeyShare (x,y) = ({}, {}) (on curve {})'.format(self.x, self.y, self.curve_params._name)


class EncryptedMessage(ThresholdDataClass):
    # TODO include curve_params?
    """
    An encrypted message in the scheme. Because a hybrid approach is used it consists of three parts:

    - C1 = kP as in the ElGamal scheme
    - C2 = kQ + rP as in the ElGamal scheme with rP being the encrypted point for a random value r
    - ciphertext, the symmetrically encrypted message.

    The symmetric key is derived from the ElGamal encrypted point rP.

    Note: The ECIES approach for ECC
    - chooses a random r,
    - computes R=rP and S=rQ,
    - derives a symmetric key k from S,
    - uses R and the symmetric encryption of m as ciphertext.
    But to enable the re-encryption of ciphertexts, here the approach similar to regular ElGamal is used instead.
    """

    def __init__(self, C1: ECC.EccPoint, C2: ECC.EccPoint, ciphertext: bytes):
        """
        Construct a encrypted message.

        :param v: like in ElGamal scheme
        :param c: like in ElGamal scheme
        :param ciphertext: the symmetrically encrypted message
        """
        self.C1 = C1
        self.C2 = C2
        self.ciphertext = ciphertext

    def __eq__(self, other):
        return (isinstance(other, self.__class__) and
                self.C1 == other.C1 and
                self.C2 == other.C2 and
                self.ciphertext == other.ciphertext)

    def __str__(self):
        return 'EncryptedMessage (C1, C2, ciphertext) = ({}, {}, {}))'.format(self.C1, self.C2, self.ciphertext)


class LagrangeCoefficient(ThresholdDataClass):
    """
    The Lagrange coefficient for a distinct participant used in partial decryption combination and partial re-encryption key combination.
    """

    def __init__(self, participant_index: int, used_index_values: Iterable[int], coefficient: int):
        """
        Construct the Lagrange coefficient

        :param participant_index: the index (=x value) for the participants share
        :param used_index_values: all used indices for reconstruction
        :param coefficient: the computed Lagrange coefficient for participant_index using used_index_values
        """
        self.participant_index = participant_index
        self.used_index_values = set(used_index_values)
        self.coefficient = coefficient

    def __eq__(self, other):
        return (isinstance(other, self.__class__) and
                self.participant_index == other.participant_index and
                self.used_index_values == other.used_index_values and
                self.coefficient == other.coefficient)

    def __str__(self):
        return 'LagrangeCoefficient for participant with index {} in group {} : {}'.format(self.participant_index, list(self.used_index_values), self.coefficient)


class PartialDecryption(ThresholdDataClass):
    """
    A partial decryption of an encrypted message computed by a share owner using his share.
    """

    def __init__(self, x: int, yC1: ECC.EccPoint, curve_params: CurveParameters):
        """
        Construct the partial decryption.

        :param x: the shares x value
        :param yC1: the computed partial decryption value
        """
        self.x = x
        self.yC1 = yC1
        self.curve_params = curve_params

    def __eq__(self, other):
        return (isinstance(other, self.__class__) and
                self.x == other.x and
                self.yC1 == other.yC1 and
                self.curve_params == other.curve_params)

    def __str__(self):
        return 'PartialDecryption (x, yC1) = ({}, {}) (on curve {})'.format(self.x, self.yC1, self.curve_params._name)


class PartialReEncryptionKey(ThresholdDataClass):
    """
    A partial re-encryption key, which can be combined with others to yield the final re-encryption key.
    """

    def __init__(self, partial_key: int, curve_params: CurveParameters):
        """
        Construct a partial re-encryption key.

        :param partial_key: The difference of (λ2_i * y2_i - λ1_i * y1_i) where *1 are the old and *2 the new components
        :param curve_params: The used curve parameters
        """
        if partial_key < 0 or partial_key > curve_params.order:
            raise ThresholdCryptoError('Invalid partial key')

        self.partial_key = partial_key
        self.curve_params = curve_params

    def __eq__(self, other):
        return (isinstance(other, self.__class__) and
                self.partial_key == other.partial_key and
                self.curve_params == other.curve_params)

    def __str__(self):
        return 'PartialReEncryptionKey λ2_i * y2_i - λ1_i * y1_i = {} (for curve {})'.format(self.partial_key, self.curve_params._name)


class ReEncryptionKey(ThresholdDataClass):
    """
    The re-encryption key created from combined partial re-encryption keys. It can be used to re-encrypt ciphertexts
    encrypted for access structure A to ciphertexts decryptable by access structure B.
    """

    def __init__(self, key: int, curve_params: CurveParameters):
        """
        Construct the re-encryption key

        :param key: the reencryption key (dB - dA) meaning new private key minus old private key obtained by combing partial re-encryption keys
        :param curve_params: The used curve parameters
        """
        if key < 0 or key > curve_params.order:
            raise ThresholdCryptoError('Invalid re-encryption key')

        self.key = key
        self.curve_params = curve_params

    def __eq__(self, other):
        return (isinstance(other, self.__class__) and
                self.key == other.key and
                self.curve_params == other.curve_params)

    def __str__(self):
        return 'ReEncryptionKey dB - dA = {} (for curve {})'.format(self.key, self.curve_params._name)
